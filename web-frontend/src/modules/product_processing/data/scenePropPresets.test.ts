import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_SCENE_STYLE,
  GRID_PANEL_STYLES,
  PROP_ASSETS,
  PROP_MATERIAL_FAMILY,
  PROP_POOLS,
  PROP_PRESETS,
  PROP_STYLE_LIMITS,
  buildScenePropLine,
  resolvePropConflicts,
  validateScenePropLine,
} from './scenePropPresets.ts';

test('每类默认 3 件，候选池不少于默认且不超过 6 件', () => {
  for (const [categoryId, props] of Object.entries(PROP_PRESETS)) {
    assert.equal(props.length, 3, `${categoryId} 默认应为 3 件`);
    const pool = PROP_POOLS[categoryId as keyof typeof PROP_POOLS];
    assert.ok(pool.length >= props.length, `${categoryId} 候选池少于默认`);
    assert.ok(pool.length <= PROP_STYLE_LIMITS.ornate, `${categoryId} 候选池超过 6 件`);
    // 默认三件必须就是候选池的前三件，避免两处维护跑偏
    assert.deepEqual(pool.slice(0, 3), props, `${categoryId} 默认与候选池前 3 件不一致`);
  }
});

test('场景风格决定零件数量：极简 1 / 生活 3 / 智能 4 / 花哨 6', () => {
  assert.equal(resolvePropConflicts('home_kitchen', {}, { style: 'minimal' }).length, 1);
  assert.equal(resolvePropConflicts('home_kitchen', {}, { style: 'lifestyle' }).length, 3);
  assert.equal(resolvePropConflicts('home_kitchen', {}, { style: 'smart' }).length, 4);
  assert.equal(resolvePropConflicts('home_kitchen', {}, { style: 'ornate' }).length, 6);
});

test('花哨档固定 6 件，且同材质不重复', () => {
  for (const categoryId of Object.keys(PROP_POOLS)) {
    const props = resolvePropConflicts(categoryId, {}, { style: 'ornate' });
    assert.equal(props.length, PROP_STYLE_LIMITS.ornate, `${categoryId} 花哨档应满 6 件`);
    const families = props.map((prop) => PROP_MATERIAL_FAMILY[prop] || prop);
    assert.equal(new Set(families).size, families.length, `${categoryId} 出现同材质零件: ${props}`);
  }
});

test('home_kitchen 默认拼装出场景与三件零件（带摆位）', () => {
  assert.equal(
    buildScenePropLine('home_kitchen'),
    'Scene: warm oak countertop, warm gray plaster wall, shallow depth; '
      + 'Set: upper-left walnut cutting board, lower-right white ceramic dish, upper-right eucalyptus sprig;',
  );
  assert.equal(DEFAULT_SCENE_STYLE, 'lifestyle');
});

test('摆位可以整体关闭，回到纯零件名', () => {
  assert.equal(
    buildScenePropLine('home_kitchen', {}, { placements: false }),
    'Scene: warm oak countertop, warm gray plaster wall, shallow depth; '
      + 'Set: walnut cutting board, white ceramic dish, eucalyptus sprig;',
  );
});

test('素材齐备时切换成贴图指代，提示词更短', () => {
  const props = resolvePropConflicts('home_kitchen');
  props.forEach((prop) => {
    PROP_ASSETS[prop] = `/assets/props/${prop}.png`;
  });
  try {
    const line = buildScenePropLine('home_kitchen');
    assert.match(line, /; Set: supplied prop cutouts;$/);
  } finally {
    props.forEach((prop) => {
      delete PROP_ASSETS[prop];
    });
  }
});

test('主体是木制品时，木类零件换成石板', () => {
  const props = resolvePropConflicts('home_kitchen', { title: '实木置物架' });
  assert.ok(!props.includes('walnut cutting board'));
  assert.ok(props.includes('warm gray stone slab'));
  assert.ok(props.length <= PROP_STYLE_LIMITS.lifestyle);
});

test('商品本体就是某零件时直接移除该零件', () => {
  const props = resolvePropConflicts('sports_outdoor', { title: '加厚瑜伽垫' });
  assert.ok(!props.includes('rolled yoga mat'));
  // 生活档 3 件：删掉瑜伽垫后，用候补件补满
  assert.deepEqual(props, ['washed linen cloth', 'warm gray stone slab', 'light oak block']);
});

test('超出风格上限会截断', () => {
  const props = resolvePropConflicts('general', {}, {
    style: 'lifestyle',
    props: ['light oak block', 'white ceramic dish', 'brass clip', 'frosted glass cup'],
  });
  assert.equal(props.length, PROP_STYLE_LIMITS.lifestyle);
});

test('零件全部被冲突规则移除时，只输出场景段', () => {
  const line = buildScenePropLine('general', {}, { props: [] });
  assert.equal(
    line,
    'Scene: neutral plaster corner, pale gray paper backdrop, soft floor transition, shallow depth;',
  );
});

test('花哨档的品类段仍控制在 38 词内', () => {
  for (const categoryId of Object.keys(PROP_POOLS)) {
    const line = buildScenePropLine(categoryId, {}, { style: 'ornate' });
    assert.ok(line.split(' ').length <= 38, `${categoryId} 过长: ${line}`);
  }
});

test('未知品类回落到 general', () => {
  assert.equal(buildScenePropLine('not_a_category'), buildScenePropLine('general'));
});

test('主体是白色时删掉白色陶瓷盘，并从候选池补位', () => {
  const props = resolvePropConflicts('home_kitchen', { title: '白色收纳盒' });
  assert.ok(!props.includes('white ceramic dish'));
  assert.equal(props.length, 3);
  assert.ok(props.includes('warm gray stone slab'));
});

test('仿木纹这类描述不触发木质换件', () => {
  const props = resolvePropConflicts('home_kitchen', { title: '仿木纹塑料收纳盒' });
  assert.ok(props.includes('walnut cutting board'));
});

test('同一零件只有一种写法：通用兜底也使用 warm gray stone slab', () => {
  assert.ok(PROP_POOLS.general.includes('warm gray stone slab'));
  assert.ok(!PROP_POOLS.general.includes('stone slab'));
});

test('四格映射：主图与微距不出零件，智能档第 4 格升级花哨', () => {
  assert.deepEqual(GRID_PANEL_STYLES.ornate, [null, null, 'ornate', 'ornate']);
  assert.deepEqual(GRID_PANEL_STYLES.smart, [null, null, 'lifestyle', 'ornate']);
  assert.deepEqual(GRID_PANEL_STYLES.minimal, [null, null, 'minimal', 'minimal']);
});

test('所有品类在四个档位下都不超词预算', () => {
  for (const style of ['minimal', 'lifestyle', 'smart', 'ornate'] as const) {
    for (const categoryId of Object.keys(PROP_POOLS)) {
      const result = validateScenePropLine(buildScenePropLine(categoryId, {}, { style }), style);
      assert.equal(
        result.overBudget,
        false,
        `${style}/${categoryId} 超预算 ${result.words}/${result.budget}`,
      );
    }
  }
});
