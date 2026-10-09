// MainPG-Updater.exe : lightweight offline patch applier for MainPG onedir.
// Compiled with the .NET Framework csc.exe (C# 5 / .NET 4.0) - keep syntax compatible.
//
// Flow: the workbench (Python) downloads and verifies patch files, writes a
// plain-text state file, then launches this updater with --apply and exits.
// This updater applies replace/add/delete, backs up replaced files, writes the
// new version.json, and starts MainPG.exe.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace MainPGUpdater
{
    internal class PatchEntry
    {
        public string Action = "";
        public string Path = "";
        public string Sha256 = "";
        public long Size = 0;
    }

    internal class PatchState
    {
        public string BaseDir = "";
        public string StagingDir = "";
        public string ToVersion = "";
        public List<PatchEntry> Entries = new List<PatchEntry>();
    }

    internal class Program
    {
        private const string StateHeader = "# mainpg-patch-state v1";
        private static string _rootDir = "";
        private static string _backupDir = "";
        private static string _logFile = "";
        private static readonly List<string> _backedUp = new List<string>(); // absolute target paths that have a backup

        private static int Main(string[] args)
        {
            try
            {
                if (args.Length >= 1 && (args[0] == "--help" || args[0] == "-h"))
                {
                    Console.WriteLine("MainPG-Updater --apply <state-file>");
                    return 0;
                }
                if (args.Length < 2 || args[0] != "--apply")
                {
                    return 1; // nothing to do: caller decided not to update
                }
                string statePath = args[1];
                if (!File.Exists(statePath))
                {
                    Log("state file missing: " + statePath);
                    return 2;
                }
                PatchState state;
                try
                {
                    state = ParseState(statePath);
                }
                catch (Exception ex)
                {
                    Log("state parse failed: " + ex.Message);
                    return 2;
                }
                if (!InitDirectories(state))
                {
                    return 2;
                }
                // The workbench schedules its own exit shortly after launching us;
                // wait until it is gone so the onedir DLLs/PYDs are unlocked.
                WaitForMainPGExit();
                bool applied = false;
                try
                {
                    ApplyState(state);
                    applied = true;
                }
                catch (Exception ex)
                {
                    Log("apply failed: " + ex.Message);
                    Rollback();
                }
                if (applied)
                {
                    try { File.Delete(statePath); } catch { }
                }
                // Always relaunch: the workbench exited before we started, so
                // leaving it down (even after a rollback) would strand the user.
                LaunchApp(state.BaseDir);
                return applied ? 0 : 3;
            }
            catch (Exception ex)
            {
                try { Log("updater fatal: " + ex.ToString()); } catch { }
                return 3;
            }
        }

        private static bool InitDirectories(PatchState state)
        {
            if (state.BaseDir.Length == 0 || !Directory.Exists(state.BaseDir))
            {
                Log("invalid base dir: " + state.BaseDir);
                return false;
            }
            if (state.StagingDir.Length == 0 || !Directory.Exists(state.StagingDir))
            {
                Log("invalid staging dir: " + state.StagingDir);
                return false;
            }
            if (!File.Exists(Path.Combine(state.BaseDir, "MainPG.exe")))
            {
                Log("base dir does not look like an installation: " + state.BaseDir);
                return false;
            }
            _rootDir = state.BaseDir;
            _backupDir = Path.Combine(_rootDir, "updates", ".backup", "rollback-" + DateTime.UtcNow.Ticks.ToString());
            _logFile = Path.Combine(_rootDir, "updates", "updater.log");
            Directory.CreateDirectory(_backupDir);
            Directory.CreateDirectory(Path.GetDirectoryName(_logFile));
            return true;
        }

        private static PatchState ParseState(string statePath)
        {
            PatchState state = new PatchState();
            bool inFiles = false;
            foreach (string raw in File.ReadAllLines(statePath, Encoding.UTF8))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#"))
                {
                    continue;
                }
                if (line == "---")
                {
                    inFiles = true;
                    continue;
                }
                if (!inFiles)
                {
                    int eq = line.IndexOf('=');
                    if (eq <= 0)
                    {
                        continue;
                    }
                    string key = line.Substring(0, eq).Trim();
                    string value = line.Substring(eq + 1).Trim();
                    if (key == "base_dir")
                    {
                        state.BaseDir = value;
                    }
                    else if (key == "staging_dir")
                    {
                        state.StagingDir = value;
                    }
                    else if (key == "to_version")
                    {
                        state.ToVersion = value;
                    }
                    continue;
                }
                // file entry: action|relative_path|sha256|size   (sha256/size optional for delete)
                string[] parts = line.Split('|');
                if (parts.Length < 2)
                {
                    continue;
                }
                PatchEntry entry = new PatchEntry();
                entry.Action = parts[0].Trim();
                entry.Path = parts[1].Trim();
                if (parts.Length >= 3)
                {
                    entry.Sha256 = parts[2].Trim();
                }
                if (parts.Length >= 4)
                {
                    long size;
                    if (long.TryParse(parts[3].Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out size))
                    {
                        entry.Size = size;
                    }
                }
                if (!RelSafe(entry.Path) || (entry.Action != "add" && entry.Action != "replace" && entry.Action != "delete"))
                {
                    Log("invalid patch entry: " + line);
                    throw new Exception("invalid patch entry");
                }
                state.Entries.Add(entry);
            }
            if (state.BaseDir.Length == 0 || state.StagingDir.Length == 0 || state.ToVersion.Length == 0 || state.Entries.Count == 0)
            {
                throw new Exception("state file is incomplete");
            }
            return state;
        }

        private static void ApplyState(PatchState state)
        {
            // 1. pre-backup every target file that currently exists, so rollback can restore it
            foreach (PatchEntry entry in state.Entries)
            {
                string target = Path.Combine(state.BaseDir, entry.Path);
                if (entry.Action == "delete" || entry.Action == "replace")
                {
                    if (File.Exists(target))
                    {
                        Backup(target);
                    }
                }
            }
            // 2. apply
            foreach (PatchEntry entry in state.Entries)
            {
                string target = Path.Combine(state.BaseDir, entry.Path);
                if (entry.Action == "delete")
                {
                    if (File.Exists(target))
                    {
                        DeleteRetry(target);
                        Log("delete " + entry.Path);
                    }
                    continue;
                }
                string staged = Path.Combine(state.StagingDir, entry.Path);
                if (!File.Exists(staged))
                {
                    throw new Exception("staged file missing: " + entry.Path);
                }
                byte[] bytes = File.ReadAllBytes(staged);
                if (entry.Sha256.Length > 0)
                {
                    string actual = Sha256Hex(bytes);
                    if (actual != entry.Sha256)
                    {
                        throw new Exception("sha256 mismatch for " + entry.Path);
                    }
                }
                string parent = Path.GetDirectoryName(target);
                if (!Directory.Exists(parent))
                {
                    Directory.CreateDirectory(parent);
                }
                WriteAllBytesRetry(target, bytes);
                Log((entry.Action == "add" ? "add    " : "replace") + " " + entry.Path);
            }
            // 3. write new version.json
            string versionFile = Path.Combine(state.BaseDir, "version.json");
            File.WriteAllText(versionFile, "{\"version\":\"" + JsonEscape(state.ToVersion) + "\"}\n", new UTF8Encoding(false));
            Log("version -> " + state.ToVersion);
        }

        private static void Backup(string target)
        {
            string rel = target.Substring(_rootDir.Length).TrimStart('\\', '/');
            string dest = Path.Combine(_backupDir, rel);
            string parent = Path.GetDirectoryName(dest);
            if (!Directory.Exists(parent))
            {
                Directory.CreateDirectory(parent);
            }
            File.Copy(target, dest, true);
            _backedUp.Add(target);
        }

        private static void WaitForMainPGExit(int timeoutSeconds = 90)
        {
            long deadline = DateTime.UtcNow.Ticks + timeoutSeconds * TimeSpan.TicksPerSecond;
            while (DateTime.UtcNow.Ticks < deadline)
            {
                if (!IsMainPGRunning())
                {
                    return;
                }
                Thread.Sleep(250);
            }
            Log("MainPG.exe did not exit within " + timeoutSeconds + "s; applying anyway");
        }

        private static bool IsMainPGRunning()
        {
            try
            {
                foreach (Process process in Process.GetProcessesByName("MainPG"))
                {
                    try
                    {
                        if (process.MainModule != null &&
                            string.Equals(process.MainModule.FileName, Path.Combine(_rootDir, "MainPG.exe"), StringComparison.OrdinalIgnoreCase))
                        {
                            return true;
                        }
                    }
                    catch
                    {
                        return true; // cannot read the module path; assume it is running
                    }
                    finally
                    {
                        process.Dispose();
                    }
                }
            }
            catch
            {
                return true; // on failure, assume still running to stay safe
            }
            return false;
        }

        private static void WriteAllBytesRetry(string path, byte[] bytes)
        {
            for (int attempt = 0; attempt < 10; attempt++)
            {
                try
                {
                    File.WriteAllBytes(path, bytes);
                    return;
                }
                catch (IOException)
                {
                    if (attempt == 9)
                    {
                        throw;
                    }
                    Thread.Sleep(300);
                }
            }
        }

        private static void DeleteRetry(string path)
        {
            for (int attempt = 0; attempt < 10; attempt++)
            {
                try
                {
                    File.Delete(path);
                    return;
                }
                catch (IOException)
                {
                    if (attempt == 9)
                    {
                        throw;
                    }
                    Thread.Sleep(300);
                }
            }
        }

        private static void Rollback()
        {
            // restore every backed-up file; delete files that were newly added (no backup)
            try
            {
                if (!Directory.Exists(_backupDir))
                {
                    return;
                }
                foreach (string target in _backedUp)
                {
                    string rel = target.Substring(_rootDir.Length).TrimStart('\\', '/');
                    string src = Path.Combine(_backupDir, rel);
                    if (File.Exists(src))
                    {
                        string parent = Path.GetDirectoryName(target);
                        if (!Directory.Exists(parent))
                        {
                            Directory.CreateDirectory(parent);
                        }
                        File.Copy(src, target, true);
                    }
                }
                Log("rollback completed");
            }
            catch (Exception ex)
            {
                Log("rollback failed: " + ex.Message);
            }
        }

        private static void LaunchApp(string baseDir)
        {
            string exe = Path.Combine(baseDir, "MainPG.exe");
            if (!File.Exists(exe))
            {
                Log("MainPG.exe missing after update");
                return;
            }
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = exe,
                    WorkingDirectory = baseDir,
                    UseShellExecute = false
                });
                Log("launched MainPG.exe");
            }
            catch (Exception ex)
            {
                Log("failed to launch MainPG.exe: " + ex.Message);
            }
        }

        private static bool RelSafe(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return false;
            }
            if (path.IndexOf('\\') >= 0 || path.IndexOf(':') >= 0 || path[0] == '/' || path[0] == '\\')
            {
                return false;
            }
            foreach (string part in path.Split('/'))
            {
                if (part.Length == 0 || part == "." || part == "..")
                {
                    return false;
                }
            }
            return true;
        }

        private static string Sha256Hex(byte[] bytes)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(bytes);
                StringBuilder sb = new StringBuilder(digest.Length * 2);
                for (int i = 0; i < digest.Length; i++)
                {
                    sb.Append(digest[i].ToString("x2"));
                }
                return sb.ToString();
            }
        }

        private static string JsonEscape(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        private static void Log(string message)
        {
            try
            {
                if (_logFile.Length == 0)
                {
                    return;
                }
                string parent = Path.GetDirectoryName(_logFile);
                if (!Directory.Exists(parent))
                {
                    Directory.CreateDirectory(parent);
                }
                File.AppendAllText(_logFile, DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ") + " " + message + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
                // logging must never break the updater
            }
        }
    }
}
