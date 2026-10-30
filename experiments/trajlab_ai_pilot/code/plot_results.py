"""Generate diagnostic plots; one plot per figure, using default colors."""
from pathlib import Path
import csv
import matplotlib.pyplot as plt

def make_plots(root):
    root=Path(root)
    rows=list(csv.DictReader((root/'data/scene_metrics.csv').open()))
    move=[r for r in rows if r['ai_decision']=='path']
    fig,ax=plt.subplots(figsize=(7.3,6.5))
    ax.scatter([100*float(r['straight_mean_frechet']) for r in move],[100*float(r['ai_mean_frechet']) for r in move],s=65,alpha=.8,label='One point per scene')
    ax.plot([0,16],[0,16],'--',linewidth=1.3,label='Equal geometric distance')
    for r in move:
        if int(r['index']) in [3,7,9,12,25]:
            ax.annotate(r['index'],(100*float(r['straight_mean_frechet']),100*float(r['ai_mean_frechet'])),xytext=(5,5),textcoords='offset points',fontsize=9)
    ax.set(xlabel='Straight line vs. humans (% of image diagonal)',ylabel='AI proposal vs. humans (% of image diagonal)',title='Reference-conditioned pilot: geometric comparison\n31 movement scenes; lower is closer to human drawings',xlim=(0,16),ylim=(0,16))
    ax.legend(loc='upper left');ax.grid(alpha=.2);fig.tight_layout();fig.savefig(root/'figures/geometry_comparison.png',dpi=180);plt.close(fig)
    vals=list(csv.DictReader((root/'data/threshold_sensitivity.csv').open()))
    fig,ax=plt.subplots(figsize=(7.5,4.7))
    xs=[100*float(r['threshold_diagonal']) for r in vals]
    ax.plot(xs,[100*float(r['ai_human_within_threshold_macro_31_scenes']) for r in vals],'o-',label='AI vs. humans')
    ax.plot(xs,[100*float(r['straight_human_within_threshold_macro_same_31_scenes']) for r in vals],'s--',label='Straight line vs. humans')
    ax.set(xlabel='Distance tolerance (% of image diagonal)',ylabel='Comparisons within tolerance (%)',title='Agreement depends on the geometric tolerance\nScene-balanced averages on the same 31 movement scenes',ylim=(0,100),xticks=xs)
    ax.legend();ax.grid(alpha=.2);fig.tight_layout();fig.savefig(root/'figures/threshold_sensitivity.png',dpi=180);plt.close(fig)
    return [root/'figures/geometry_comparison.png',root/'figures/threshold_sensitivity.png']
if __name__=='__main__':make_plots(Path(__file__).resolve().parents[1])
