"""Latest cumulative coverage and cost of iteration; no causal comparison.

Structural adaptation of project runs/fig113.py; original plot is unchanged.
Unit: 40 distinct tasks per arm. Multi includes all three approved followups.
One execution series; no uncertainty bars/significance claims are available.
The fourth panel counts unresolved task coverage, NOT human intervention.
"""
from pathlib import Path
import sys, os, json
root=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(root/'runs/plotdeps'))
sys.path.insert(0,'C:/Users/lenovo/.codex/skills/nature-figure/scripts')
os.environ['MPLCONFIGDIR']=str(root/'runs/mplconfig')
os.environ['MPLBACKEND']='Agg'
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from audit_panel_alignment import require_matplotlib_panel_alignment

out=Path(__file__).resolve().parent
data=json.loads((out/'latest-metrics.json').read_text(encoding='utf-8'))
rows=data['rows']
for row in rows:
    row['latest_unresolved']=row['planned']-row['success']
    row['latest_unresolved_percent']=100*row['latest_unresolved']/row['planned']
    assert row['planned']==40
assert [r['success'] for r in rows]==[38,40]
font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
plt.rcParams.update({'font.family':'Arial','svg.fonttype':'none','pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False,'font.size':12})
fig, axes=plt.subplots(2,2,figsize=(12,8))
fig.subplots_adjust(left=.09,right=.96,bottom=.23,top=.82,wspace=.28,hspace=.70)
panels=[('a','累计成功覆盖率（%）','success_percent',1,120),
        ('b','平均累计模型请求（次/题）','average_requests',3,3.8),
        ('c','平均累计任务耗时（秒/题）','average_seconds',3,58),
        ('d','最新尚未通过题目比例（%）','latest_unresolved_percent',1,6.5)]
for ax,(letter,title,key,digits,limit) in zip(axes.flat,panels):
    values=[r[key] for r in rows]
    ax.bar([0,1],values,color=['#3566b7','#138675'],width=.5)
    ax.set_xticks([0,1],['单Agent\n有界迭代基线','多Agent\n四角色证据协作'],fontproperties=font,fontsize=12)
    ax.set_title(title,fontproperties=font,loc='left',pad=15,fontsize=13)
    ax.text(-.13,1.12,letter,transform=ax.transAxes,fontweight='bold',fontsize=14)
    ax.set_ylim(0,limit);ax.set_axisbelow(True)
    for i,n in enumerate(values):
        ax.text(i,n+limit*.06,f'{n:.{digits}f}',ha='center',va='bottom',fontsize=12)
fig.text(.09,.94,'最新累计修复成果与迭代消耗',fontproperties=font,fontsize=20,weight='bold')
fig.text(.09,.89,'两组各40题；多Agent含3次关联补跑，原运行与补跑消耗均计入',fontproperties=font,fontsize=12)
fig.text(.09,.13,'补跑版本及资源上限发生变化，本图不是同条件首试因果对照。',fontproperties=font,fontsize=11)
fig.text(.09,.09,'d为最新尚未通过题目比例，非历史移交包比例，非人工实际介入率。',fontproperties=font,fontsize=11)
fig.text(.09,.05,'来源：平台最新记录只读导出；原始80条运行与3条恢复均保留。',fontproperties=font,fontsize=10,color='#526175')
fig.canvas.draw()
require_matplotlib_panel_alignment(fig,json_out=out/'panel-alignment.json',panel_ids=['a','b','c','d'])
fig.savefig(out/'latest-four-metrics.png',dpi=600)
fig.savefig(out/'latest-four-metrics.svg')
fig.savefig(out/'latest-four-metrics.pdf')
print('Latest cumulative four-panel figure exported, alignment gate completed.')
