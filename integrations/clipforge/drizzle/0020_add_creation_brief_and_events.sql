CREATE TABLE `project_events` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`kind` text NOT NULL,
	`payload` text,
	`created_at` integer
);
--> statement-breakpoint
ALTER TABLE `projects` ADD `creation_brief` text;