const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Event-level regression only: this stub does not render HTML or emulate a browser.
const html = fs.readFileSync(path.join(__dirname, '../ui/prototype/index.html'), 'utf8');

function loadPrototype() {
  const elements = new Map();
  let externalCalls = 0;
  let downloads = 0;
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const [, tag, attributes, id] = match;
    const handlers = new Map();
    let value = attributes.match(/\bvalue="([^"]*)"/)?.[1] || '';
    if (tag === 'textarea') value = html.slice(match.index + match[0].length).split('</textarea>')[0];
    if (tag === 'select') {
      const option = html.slice(match.index + match[0].length).split('</select>')[0].match(/<option(?:[^>]*value="([^"]*)")?[^>]*>([^<]*)/);
      value = option?.[1] ?? option?.[2] ?? '';
    }
    const classes = new Set();
    const element = {
      id, value, disabled: /\sdisabled\b/.test(attributes), checked: /\schecked\b/.test(attributes),
      textContent: '', innerHTML: '', className: '', style: {}, hidden: false, open: false,
      classList: { add: item => classes.add(item), remove: item => classes.delete(item), contains: item => classes.has(item) },
      addEventListener(type, handler) { handlers.set(type, [...(handlers.get(type) || []), handler]); },
      emit(type) { for (const handler of handlers.get(type) || []) handler({ target: element }); }
    };
    elements.set(id, element);
  }
  const document = {
    getElementById: id => { assert.ok(elements.has(id), `Missing element ${id}`); return elements.get(id); },
    querySelectorAll: selector => selector.split(',').map(item => elements.get(item.trim().slice(1))).filter(Boolean),
    createElement: () => { downloads++; return { click() {} }; }
  };
  const context = vm.createContext({
    document, Date, URL: { createObjectURL: () => 'stub:blob', revokeObjectURL() {} }, Blob,
    fetch: () => { externalCalls++; throw new Error('Network call forbidden in static prototype'); },
    prompt: () => { throw new Error('Unsupported constraint action must stay disabled'); }
  });
  for (const script of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) vm.runInContext(script[1], context);
  const get = id => document.getElementById(id);
  return {
    get,
    click: id => get(id).emit('click'), // Deliberately dispatches even when disabled to check handler guards.
    change(id, value, event = 'input') { get(id).value = String(value); get(id).emit(event); },
    scenario(value) { get('demoState').value = value; get('demoState').emit('change'); },
    sideEffects: () => ({ externalCalls, downloads })
  };
}

function previewAndStart(ui) { ui.click('previewBtn'); ui.click('startBtn'); }

test('active demo cannot be reset or restarted, including direct handler dispatch', () => {
  for (const state of ['running', 'relocating', 'waiting', 'paused']) {
    const ui = loadPrototype();
    previewAndStart(ui);
    ui.scenario(state);
    const runId = ui.get('runIdLabel').textContent;
    assert.equal(ui.get('newRunBtn').disabled, true, state);
    assert.equal(ui.get('startBtn').disabled, true, state);
    ui.click('newRunBtn');
    ui.click('startBtn');
    assert.equal(ui.get('runIdLabel').textContent, runId, state);
    ui.scenario('idle');
    assert.equal(ui.get('runIdLabel').textContent, runId, `${state}: idle selector must not erase active state`);
  }
});

test('editing invalidates preview; startup consumes the reviewed snapshot', () => {
  const ui = loadPrototype();
  ui.click('previewBtn');
  const snapshot = JSON.parse(ui.get('previewEvidence').textContent);
  assert.equal(snapshot.task_id, 'T001-leap');
  assert.equal(snapshot.max_repair_rounds, 3);
  ui.change('maxRounds', '2');
  assert.equal(ui.get('startBtn').disabled, true);
  ui.click('startBtn');
  assert.match(ui.get('runIdLabel').textContent, /未创建/);
  ui.click('previewBtn');
  ui.click('startBtn');
  assert.equal(ui.get('roundBudget').textContent, '1 / 2');
  assert.equal(ui.get('maxRounds').disabled, true);
  assert.equal(JSON.parse(ui.get('previewEvidence').textContent).max_repair_rounds, 2);
});

test('invalid fields and unsupported roles do not receive a successful preview', () => {
  const invalidCases = [
    ui => ui.change('maxRounds', '0'), ui => ui.change('maxRounds', '4'),
    ui => ui.change('maxRounds', '1.5'), ui => ui.change('description', ''),
    ui => ui.change('allowed', '../secret.py'), ui => ui.change('blocked', 'tests/\ncode/'),
    ui => { ui.get('roleLocator').checked = false; },
    ui => { ui.get('roleReviewer').checked = true; }
  ];
  for (const invalidate of invalidCases) {
    const ui = loadPrototype();
    invalidate(ui);
    ui.click('previewBtn');
    assert.equal(ui.get('startBtn').disabled, true);
    assert.match(ui.get('validation').textContent, /配置错误/);
  }
});

test('relocation preserves the failed applied candidate and highlights the next round', () => {
  const ui = loadPrototype();
  previewAndStart(ui);
  const runId = ui.get('runIdLabel').textContent;
  ui.scenario('relocating');
  assert.equal(ui.get('runIdLabel').textContent, runId);
  assert.equal(ui.get('roundBudget').textContent, '1 / 3');
  assert.equal(ui.get('roundsLeft').textContent, '2');
  assert.match(ui.get('timeline').innerHTML, /第2轮.*定位/);
  assert.match(ui.get('historyList').innerHTML, /第1轮/);
  assert.match(ui.get('historyList').innerHTML, /开发测试失败/);
  assert.match(ui.get('diffState').textContent, /已应用/);
  assert.doesNotMatch(ui.get('diffEvidence').textContent, /拒绝|未应用/);
  assert.match(ui.get('decisionEvidence').textContent, /重新定位/);
});

test('stopped snapshot reflects the configured cap and contains failure evidence', () => {
  for (const limit of [1, 3]) {
    const ui = loadPrototype();
    ui.change('maxRounds', limit);
    previewAndStart(ui);
    ui.scenario('stopped');
    assert.equal(ui.get('roundBudget').textContent, `${limit} / ${limit}`);
    assert.equal(ui.get('roundsLeft').textContent, '0');
    assert.equal(ui.get('devTestState').textContent, '失败');
    assert.match(ui.get('logEvidence').textContent, /FAILED|failed/);
    assert.match(ui.get('decisionEvidence').textContent, /停止|移交/);
    assert.equal(ui.get('newRunBtn').disabled, false);
  }
});

test('success and final-check failure have distinct consistent decisions', () => {
  const ui = loadPrototype();
  previewAndStart(ui);
  ui.scenario('success');
  assert.equal(ui.get('devTestState').textContent, '通过');
  assert.equal(ui.get('acceptState').textContent, '通过');
  assert.match(ui.get('decisionEvidence').textContent, /结束/);
  assert.doesNotMatch(ui.get('decisionEvidence').textContent, /断言失败|重新定位/);
  ui.scenario('failed');
  assert.equal(ui.get('devTestState').textContent, '通过');
  assert.equal(ui.get('acceptState').textContent, '失败');
  assert.match(ui.get('decisionEvidence').textContent, /final_check_failed/);
  assert.equal(ui.get('newRunBtn').disabled, false);
});

test('unimplemented controls remain disabled without downloads, model or tool calls', () => {
  const ui = loadPrototype();
  for (const state of ['idle', 'running', 'relocating', 'waiting', 'paused', 'failed', 'stopped', 'success']) {
    if (state !== 'idle') ui.scenario(state);
    assert.match(ui.get('modeBadge').textContent, /模拟状态.*非运行证据/);
    for (const id of ['pauseBtn', 'constraintBtn', 'reportBtn', 'patchBtn']) {
      assert.equal(ui.get(id).disabled, true, `${state}: ${id}`);
      ui.click(id);
    }
  }
  assert.deepEqual(ui.sideEffects(), { externalCalls: 0, downloads: 0 });
});

test('a terminal demo requires fresh preview and receives a new identity after reset', () => {
  const ui = loadPrototype();
  previewAndStart(ui);
  const previousId = ui.get('runIdLabel').textContent;
  ui.scenario('stopped');
  ui.click('newRunBtn');
  assert.match(ui.get('runIdLabel').textContent, /未创建/);
  ui.click('startBtn');
  assert.match(ui.get('runIdLabel').textContent, /未创建/);
  previewAndStart(ui);
  assert.notEqual(ui.get('runIdLabel').textContent, previousId);
});

test('one-round cap cannot display a second repair round', () => {
  const ui = loadPrototype();
  ui.change('maxRounds', 1);
  previewAndStart(ui);
  ui.scenario('relocating');
  assert.equal(ui.get('roundBudget').textContent, '1 / 1');
  assert.doesNotMatch(ui.get('timeline').innerHTML, /第2轮/);
  assert.match(ui.get('validation').textContent, /上限1轮/);
});
