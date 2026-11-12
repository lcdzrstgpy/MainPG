// 设计令牌回归测试：确认 AppTokens 与原型 mobile-prototype/styles.css 的 :root 变量一致。
// 原型是视觉基线，改动应先改原型再同步到这里。

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:mainpg_companion/shared/theme/app_tokens.dart';

void main() {
  test('设计令牌与原型 styles.css 的 :root 变量一致', () {
    expect(AppTokens.brand, const Color(0xFF2F6BFF));
    expect(AppTokens.bg, const Color(0xFFF6F7FB));
    expect(AppTokens.surface, const Color(0xFFFFFFFF));
    expect(AppTokens.t1, const Color(0xFF1D2129));
    expect(AppTokens.line, const Color(0xFFEEF0F4));
    expect(AppTokens.ok, const Color(0xFF00B42A));
    expect(AppTokens.warn, const Color(0xFFFF7D00));
    expect(AppTokens.danger, const Color(0xFFF53F3F));
  });
}
