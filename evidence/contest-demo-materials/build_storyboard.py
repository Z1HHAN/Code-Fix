"""Build an offline visual storyboard from archived evidence; never run tasks."""
from pathlib import Path
from html import escape
from decimal import Decimal
import csv
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BLUE, ORANGE, GREEN, GRAY = '#165bd8', '#d77712', '#16834a', '#6b788b'
INK, LIGHT = '#142a49', '#e5edf7'
slides = []


def txt(x, y, value, size=24, color=INK, weight=400, anchor='start'):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>'


def line(x1, y1, x2, y2, color=BLUE, width=3, arrow=False, dash=False):
    return f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="{color}" stroke-width="{width}"' + (f' marker-end="url(#arrow{color[1:]})"' if arrow else '') + (' stroke-dasharray="8 7"' if dash else '') + '/>'


def path(d, color=BLUE, arrow=True, dash=False):
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3"' + (f' marker-end="url(#arrow{color[1:]})"' if arrow else '') + (' stroke-dasharray="8 7"' if dash else '') + '/>'


def box(x, y, w, h, label, color=BLUE, size=24):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="white" stroke="{color}" stroke-width="2"/>' + txt(x+w/2, y+h/2+8, label, size, color, 600, 'middle')


def group(step, body):
    return f'<g class="reveal" data-step="{step}">{body}</g>'


def add(title, body, note, source, steps=0, dark=False):
    n = len(slides)+1
    bg, foreground = ('#092c69', 'white') if dark else ('white', INK)
    markers = ''.join(f'<marker id="s{n}-arrow{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="{c}"/></marker>' for c in (BLUE, ORANGE, GREEN, '#8bb7ff', '#f6a748'))
    body = body.replace('url(#arrow', f'url(#s{n}-arrow')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" role="img" aria-labelledby="t{n}">
    <defs>{markers}</defs>
    <rect width="1280" height="720" fill="{bg}"/>
    <g font-family="Microsoft YaHei, sans-serif"><title id="t{n}">{escape(title)}</title>
    {txt(64, 84, title, 38, foreground, 700)}
    {body}
    {txt(64, 651, note, 17, '#d8e5ff' if dark else GRAY)}
    {line(64, 675, 1216, 675, '#5173a8' if dark else LIGHT, 1)}
    {txt(64, 701, source, 13, '#d8e5ff' if dark else GRAY)}
    {txt(1216, 701, f'{n:02d} / 12', 14, '#d8e5ff' if dark else GRAY, anchor='end')}
    </g></svg>'''
    slides.append({'title': title, 'svg': svg, 'steps': steps, 'source': source})


def build():
    csv_path = ROOT/'evidence/contest-strategies-config-comparison/comparison/comparison.csv'
    rows = list(csv.DictReader(csv_path.read_text(encoding='utf-8-sig').splitlines()))
    coverage = json.loads((ROOT/'evidence/quixbugs-coverage-review/coverage.json').read_text(encoding='utf-8'))
    assert len(rows) == 6 and len(coverage['rows']) == 40
    totals = {}
    for strategy in ('fixed', 'dynamic'):
        selected = [r for r in rows if r['strategy'] == strategy]
        totals[strategy] = {'success': sum(r['status'] == 'success' for r in selected),
                            'requests': sum(int(r['requests']) for r in selected),
                            'estimated_cny': str(sum(Decimal(r['amount_cny']) for r in selected)),
                            'sum_elapsed_seconds': str(sum(Decimal(r['elapsed_seconds']) for r in selected))}

    # 01: title and editable closed-loop mechanism, no fabricated team identity.
    b = txt(64, 220, '验链', 86, 'white', 700)
    b += txt(68, 289, '面向Python缺陷修复的有界闭环验证', 30, '#d8e5ff')
    for i, label in enumerate(('缺陷', '候选', '测试', '裁决', '复验')):
        x = 85+i*238
        b += txt(x, 460, label, 30, 'white', 600)
        if i < 4:
            b += line(x+80, 449, x+198, 449, '#8bb7ff', arrow=True)
    b += path('M875 485V550H355V485', '#f6a748')
    b += txt(615, 585, '真实证据决定下一步', 27, '#d8e5ff', anchor='middle')
    add('证据驱动的多Agent编排与验证平台', b,
        '真实模型生成候选，独立程序依据测试证据裁决', '项目机制依据：app/workflow_v3.py；当前有效需求', dark=True)

    # 02: scenario diagram, explicitly not a live run.
    b = box(75, 300, 220, 80, '真实测试证据')
    for y, label, result, c in [(180, '语法失败', '直接重修', ORANGE), (340, '断言失败', '按规则重新定位', BLUE), (500, '环境／证据异常', '停止自动推进', ORANGE)]:
        b += path(f'M295 340H370V{y}H465', c)
        b += txt(495, y+8, label, 28, c, 600)
        b += line(760, y, 840, y, c, arrow=True)
        b += txt(875, y+8, result, 27, c)
    add('失败证据与下一步动作', b, '规则示意 · 不是一次真实运行', '依据：configs/decision_rules.json；Workflow.repair_round')

    # 03: roles and authority layers.
    b = txt(64, 170, '模型层', 23, GRAY)
    for i, label in enumerate(('修复员', '检测员', '模型调度员', '定位员（按需）')):
        b += box(210+i*250, 135, 220, 75, label, size=23)
    b += path('M820 210V300H640V330', BLUE, dash=True)
    b += txt(850, 270, '结构化建议', 21, GRAY)
    b += txt(64, 375, '裁决层', 23, GRAY)
    b += box(350, 330, 580, 90, '独立程序调度：证据＋规则＋预算', size=26)
    b += line(640, 420, 640, 510, BLUE, arrow=True)
    b += txt(665, 480, '实际执行动作', 21, BLUE)
    b += txt(64, 560, '执行层', 23, GRAY)
    b += box(350, 510, 580, 80, '可信pytest执行器与证据存储', BLUE, 26)
    add('四角色与独立程序调度', b, '虚线：模型建议；实线：程序执行。pytest执行器不使用模型', '依据：app/agents_v3.py；app/role_config.py；app/workflow_v3.py')

    # 04: a typed example from the controlled branch contract, not natural evidence.
    b = txt(64, 160, '受控示例：候选仍有断言失败', 25, ORANGE, 600)
    b += group(1, box(65, 240, 300, 125, '测试证据：断言失败', ORANGE))
    b += group(2, line(365, 303, 470, 303, BLUE, arrow=True)+box(480, 240, 300, 125, '模型建议：final_check'))
    b += group(3, txt(65, 455, '程序：失败代码不能直接复验', 24, ORANGE)+path('M630 365V465H1170V365', ORANGE))
    b += group(4, box(900, 240, 300, 125, '实际动作：按规则定位', BLUE)+txt(900, 420, '覆盖原因随Trace保存', 22, GRAY))
    b += txt(64, 540, '建议', 26, BLUE, 600)+line(150, 531, 290, 531, BLUE, arrow=True)
    b += txt(320, 540, '程序裁决', 26, BLUE, 600)+line(470, 531, 640, 531, BLUE, arrow=True)
    b += txt(670, 540, '实际动作', 26, BLUE, 600)+line(820, 531, 970, 531, BLUE, arrow=True)
    b += txt(1000, 540, '覆盖原因', 26, BLUE, 600)
    add('一条证据怎样改变下一步', b, '离线受控分支示意 · 不是B六条自然成功run中的失败案例', '依据：Workflow.repair_round；tests/test_contest_delivery.py', steps=4)

    # 05: actual boundary mechanism; tests are never mounted into candidate.
    b = ''
    for y, title in [(155, '宿主：冻结配置、管理执行、归档产物'), (315, '可信测试容器'), (485, '候选RPC容器')]:
        b += line(64, y+100, 1216, y+100, LIGHT, 1)+txt(70, y, title, 25, BLUE, 600)
    b += group(1, box(600, 120, 480, 70, '候选版本／测试版本／镜像身份'))
    b += group(2, box(600, 280, 480, 70, '原生pytest＋固定proxy＋报告生成', BLUE))
    b += group(3, box(600, 450, 480, 70, '仅候选源码与批准调用入口', ORANGE))
    b += group(3, line(720, 350, 720, 445, BLUE, arrow=True)+txt(745, 400, 'JSON请求', 23, BLUE))
    b += group(4, line(650, 450, 650, 355, BLUE, arrow=True)+txt(490, 400, 'JSON结果', 23, BLUE)+path('M1080 315H1170V155H1085', BLUE)+txt(65, 565, '无网络 · 只读挂载 · 候选侧无测试与参考答案', 26, INK, 600))
    add('候选代码与可信测试怎样隔开', b, '可信侧形成测试报告；干净final在新冻结副本运行', '依据：app/split_runner.py；docker/candidate_rpc.py；B隔离专项', steps=4)

    # 06: config provenance chain.
    b = ''
    labels = ['角色编辑', '后端校验', '有效预览', '不可变保存', 'HTTP请求', '调用证据']
    for i, label in enumerate(labels):
        x = 70+i*195
        b += box(x, 285, 165, 85, label, size=23)
        if i < 5:
            b += line(x+165, 327, x+190, 327, BLUE, arrow=True)
    b += txt(70, 180, '指令、参数和哈希贯穿同一配置版本', 30, INK, 600)
    b += txt(70, 465, '同一模型绑定不同角色，参数仍分别生效', 27, BLUE)
    b += txt(70, 525, '未经核实的参数：页面禁用，后端拒绝', 27, ORANGE)
    add('角色配置实际进入请求', b, '离线HTTP截获已核对；B非空角色覆盖真实运行待补', '依据：role_overrides.py；configuration.py；live_model.py；071专项')

    # 07: controlled syntax route, first failed round only; no fabricated run counters.
    b = txt(640, 165, '共同上限：3候选轮次／12模型请求／300秒', 26, INK, 600, 'middle')
    b += line(640, 205, 640, 570, LIGHT, 2)
    b += txt(70, 235, '固定顺序', 28, BLUE, 600)+txt(710, 235, '动态编排', 28, BLUE, 600)
    for x in (70, 710):
        b += group(1, box(x, 280, 180, 65, '修复员')+line(x+180, 312, x+220, 312, BLUE, arrow=True)+box(x+230, 280, 210, 65, '测试：语法失败', ORANGE, 22))
    b += group(2, path('M405 345V355H170V365', BLUE)+box(80, 365, 180, 65, '检测员')+line(260, 397, 310, 397, BLUE, arrow=True)+box(320, 365, 220, 65, '调度建议记录', size=22))
    b += group(2, path('M1045 345V395H910', ORANGE)+box(720, 365, 180, 65, '直接返回修复', ORANGE, 22))
    b += txt(70, 510, '该失败轮模型请求：3次', 28, BLUE, 600).replace('<text ', '<text id="fixed-model-count" ')
    b += txt(710, 510, '该失败轮模型请求：1次', 28, BLUE, 600).replace('<text ', '<text id="dynamic-model-count" ')
    b += txt(70, 585, '两组共享预算、隔离、保守暂停及最终验收规则', 25, INK)
    add('同一失败，两种路线', b, '受控语法失败示例，仅比较该轮；不计工具测试为模型请求', '依据：Workflow.repair_round；固定/动态离线分支测试', steps=3)

    # 08: received natural successful run.
    run = next(r for r in rows if r['project'] == 'qb-get_factors' and r['strategy'] == 'dynamic')
    b = txt(65, 165, 'qb-get_factors · 动态策略 · 自然首轮成功', 28, INK, 600)
    for i, (label, desc, c) in enumerate([('基线', '坏版本复现', ORANGE), ('候选', '真实模型生成', BLUE), ('开发测试', 'passed', GREEN), ('程序裁决', 'final_check', BLUE), ('干净final', 'passed', GREEN)]):
        x = 80+i*240
        b += group(i+1, f'<circle cx="{x+80}" cy="310" r="13" fill="{c}"/>'+txt(x+80, 270, label, 26, c, 600, 'middle')+txt(x+80, 365, desc, 21, INK, anchor='middle'))
        if i < 4:
            b += line(x+95, 310, x+225, 310, BLUE, arrow=True)
    b += txt(65, 480, f"1候选轮次    {run['requests']}模型请求    {run['elapsed_seconds']}秒", 31, BLUE, 600)
    b += txt(65, 545, f"估算费用 {run['amount_cny']} CNY；源码逐文件身份与C本人验收待补", 23, GRAY)
    add('一个候选怎样被判定通过', b, 'B机真实历史 · API/CLI启动 · 不是UI点击启动的证明', run['run_id']+'；原件：comparison.csv及runs对应目录', steps=5)

    # 09: truthful terminal matrix.
    b = ''
    for y, a, action, artifact in [(210, '可恢复失败', '预算允许：进入下一轮', '保留本轮证据'), (345, '轮次／调用上限', '停止自动推进', '保留停止原因'), (480, '环境／证据异常', '按规则阻塞或暂停', '形成待人工移交材料')]:
        b += txt(70, y, a, 28, ORANGE, 600)+line(345, y-10, 430, y-10, ORANGE, arrow=True)+txt(460, y, action, 26, INK)+line(830, y-10, 900, y-10, ORANGE, arrow=True)+txt(930, y, artifact, 23, INK)
    b += txt(70, 580, '移交内容：原因 · 版本 · 证据路径 · 消耗 · 下一步建议', 24, INK)
    add('失败、上限与人工出口', b, '离线受控验证已有；真实专项待补。待人工处理不表示已经有人接手', '依据：Workflow.finish；RunTrace.handoff；T-057/T-058')

    # 10: historical coverage, not a 40-pair comparison success rate.
    b = txt(65, 155, '历史遍历40题：29报告通过，11前置阻塞', 28, INK, 600)
    for i, row in enumerate(coverage['rows']):
        col, rr = i%8, i//8
        x, y = 65+col*146, 200+rr*66
        c = GREEN if row['historical_final'] == 'passed' else ORANGE
        b += f'<rect x="{x}" y="{y}" width="132" height="50" rx="3" fill="{c}" opacity=".10"/>'
        b += txt(x+7, y+21, f'{i+1:02d} '+('通过' if c==GREEN else '阻塞'), 17, c, 600)
        name = row['project'].removeprefix('qb-')
        b += txt(x+7, y+42, name[:15], 12, INK)
    b += txt(65, 575, '最新三题配对单独报告；全40题同版本配对尚未完成', 25, BLUE, 600)
    add('QuixBugs-Python全量覆盖', b, '历史批次600秒且含重跑；最新配对300秒。不是40题首尝试成功率', '来源：quixbugs-batch1.md；quixbugs-coverage-review/coverage.csv')

    # 11: pairwise raw metrics, estimates explicitly labelled.
    b = txt(65, 162, '两组均3/3通过 · 候选各3轮次 · 模型请求各9次', 29, INK, 600)
    b += txt(65, 235, '逐题估算费用 / CNY', 24, INK, 600)+txt(695, 235, '逐题run耗时 / 秒', 24, INK, 600)
    for i, project in enumerate(['qb-get_factors', 'qb-gcd', 'qb-max_sublist_sum']):
        y = 305+i*90
        pair = {r['strategy']:r for r in rows if r['project'] == project}
        name = project.removeprefix('qb-')
        b += txt(65, y, name, 19, INK)+txt(695, y, name, 19, INK)
        for j, (strategy,c) in enumerate([('fixed',BLUE),('dynamic',ORANGE)]):
            cy = y+13+j*22
            cv = float(pair[strategy]['amount_cny'])
            tv = float(pair[strategy]['elapsed_seconds'])
            b += line(260, cy, 260+cv/.035*225, cy, c, 9)+txt(505, cy+6, pair[strategy]['amount_cny'], 17, c)
            b += line(900, cy, 900+tv/60*200, cy, c, 9)+txt(1115, cy+6, f'{tv:.2f}', 17, c)
    b += line(260, 555, 485, 555, GRAY, 1)+line(900, 555, 1100, 555, GRAY, 1)
    for value in (0, .01, .02, .03):
        x = 260+value/.035*225
        b += line(x, 550, x, 560, GRAY, 1)+txt(x, 579, f'{value:g}', 14, GRAY, anchor='middle')
    for value in (0, 20, 40, 60):
        x = 900+value/60*200
        b += line(x, 550, x, 560, GRAY, 1)+txt(x, 579, value, 14, GRAY, anchor='middle')
    b += txt(65, 615, '蓝：固定    橙：动态（本页系列编码）；小样本尚不能证明策略收益', 21, INK)
    # Orange in this chart would conflict with the user's semantic palette.
    b = b.replace(ORANGE, '#5387dd').replace('橙：动态', '浅蓝：动态')
    add('效果与代价放在同一张图里', b, '所有样本保留；费用为估算。差异不作显著性或因果结论', '来源：contest-strategies-config-comparison/comparison/comparison.csv')

    # 12: findings and honest boundary.
    b = ''
    for y, a, d in [(205, '证据驱动有界分支', '不同失败对应不同路线，所有自动推进受上限约束'), (330, '建议与裁决分离', '模型建议可回查，强制规则决定允许执行的动作'), (455, '配置与结果可追溯', '指令到请求、候选到复验均保留版本和证据')]:
        b += txt(65, y, a, 33, BLUE, 600)+txt(65, y+47, d, 25, INK)
    b += txt(65, 585, '已验证范围与待验项分开；后续提升以完整新批次为准', 25, ORANGE, 600)
    add('技术贡献与验证边界', b, '待验：全40题同版本对照、真实角色覆盖、C验收及正式录屏彩排', '固定入口：当前开发任务台账；版本与演示素材索引')

    assert len(slides) == 12
    svg_dir = OUT/'storyboard-svg'
    svg_dir.mkdir(exist_ok=True)
    for i,s in enumerate(slides,1):
        (svg_dir/f'{i:02d}.svg').write_text(s['svg'],encoding='utf-8')
    sections = '\n'.join(f'<section data-max-step="{s["steps"]}" aria-label="{escape(s["title"])}">{s["svg"]}</section>' for s in slides)
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>验链 · 技术页面视觉预览</title>
    <style>*{box-sizing:border-box}body{margin:0;background:#e6ebf2;color:#142a49;font-family:"Microsoft YaHei",sans-serif}header{padding:14px 24px;background:#092c69;color:white;font-size:15px;display:flex;align-items:center;gap:16px}button{font:inherit;padding:7px 15px;cursor:pointer}#page{margin-left:auto}section{display:none;width:min(100vw,calc((100vh - 70px)*16/9));margin:0 auto}section.active{display:block}svg{display:block;width:100%;height:auto}.reveal{transition:opacity .2s}.hidden-step{opacity:.08}body.overview section{display:block;width:1280px;margin:20px auto}body.overview .hidden-step{opacity:1}@media(prefers-reduced-motion:reduce){.reveal{transition:none}}@media print{header{display:none}body{background:white}section{display:block!important;width:100%;break-after:page;margin:0}.hidden-step{opacity:1}@page{size:landscape;margin:0}}</style>
    <header><span>视觉预览 · 非正式PPTX</span><button id="prev">上一页</button><button id="next">下一步 / 下一页</button><button id="expand">展开本页</button><button id="overview">全部页面</button><span id="page"></span></header><main>'''+sections+r'''</main><script>
    const slides=[...document.querySelectorAll('section')];let at=0,step=0;
    function draw(){slides.forEach((s,i)=>s.classList.toggle('active',i===at));for(const e of slides[at].querySelectorAll('[data-step]'))e.classList.toggle('hidden-step',Number(e.dataset.step)>step);if(at===6){document.querySelector('#fixed-model-count').textContent='该失败轮模型请求：'+(step===0?0:step===1?1:3)+'次';document.querySelector('#dynamic-model-count').textContent='该失败轮模型请求：'+(step===0?0:1)+'次'}document.querySelector('#page').textContent=(at+1)+' / '+slides.length+' · 展开 '+step+'/'+slides[at].dataset.maxStep;location.hash='slide-'+(at+1)}
    function next(){if(step<Number(slides[at].dataset.maxStep)){step++}else if(at<slides.length-1){at++;step=0}draw()}
    function previous(){if(at>0){at--;step=0}draw()}
    document.querySelector('#prev').onclick=previous;document.querySelector('#next').onclick=next;document.querySelector('#expand').onclick=()=>{step=Number(slides[at].dataset.maxStep);draw()};document.querySelector('#overview').onclick=()=>document.body.classList.toggle('overview');
    document.addEventListener('keydown',e=>{if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();next()}if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();previous()}});
    const initial=/^#slide-(\d+)$/.exec(location.hash);if(initial)at=Math.max(0,Math.min(slides.length-1,Number(initial[1])-1));draw();
    </script></html>'''
    (OUT/'technical-storyboard.html').write_text(html,encoding='utf-8')
    (OUT/'storyboard-data.json').write_text(json.dumps({'scope':'offline visual storyboard, not final PPTX or independent acceptance',
        'slides':12,'model_requests':0,'task_starts':0,'comparison_totals':totals,'historical_coverage':coverage['historical_batch']},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'slides':len(slides),'html':str(OUT/'technical-storyboard.html'),'paid_requests':0},ensure_ascii=False))


if __name__ == '__main__':
    build()
