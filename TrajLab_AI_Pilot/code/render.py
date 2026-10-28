"""Render precise image-coordinate annotations; never generate/replace scene pixels.
Usage: python code/render.py --source /path/to/extracted_original_export --root .
"""
import argparse,csv,html,json,math,textwrap
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageOps
from analyze import dense_curve

AI=(0,220,240);HUMAN=(255,182,58);INK=(18,31,47);PAPER=(247,249,252)
def font(size,bold=False):
    name='DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    for choice in [name,'/usr/share/fonts/truetype/dejavu/'+name]:
        try:return ImageFont.truetype(choice,size)
        except OSError:pass
    return ImageFont.load_default()

def wrap(draw,text,width,f):
    words=text.split();lines=[];line=''
    for word in words:
        nxt=(line+' '+word).strip()
        if draw.textlength(nxt,font=f)>width and line:lines.append(line);line=word
        else:line=nxt
    return lines+[line] if line else lines

def draw_dashed(draw,points,fill,width,dash=11,gap=7):
    # Fixed-length dashes along the curve, not one dash per sampled segment.
    draw_on=True;remaining=dash
    for a,b in zip(points[:-1],points[1:]):
        ax,ay=a;bx,by=b;dist=math.hypot(bx-ax,by-ay)
        if dist==0:continue
        ux,uy=(bx-ax)/dist,(by-ay)/dist
        while dist>1e-6:
            step=min(dist,remaining);nx,ny=ax+ux*step,ay+uy*step
            if draw_on:draw.line([(ax,ay),(nx,ny)],fill=fill,width=width)
            ax,ay=nx,ny;dist-=step;remaining-=step
            if remaining<1e-6:
                draw_on=not draw_on;remaining=dash if draw_on else gap

def markers(im,p):
    d=ImageDraw.Draw(im);w,h=im.size;k=w/1280
    for pt,label,c in [(p['start'],'S',(44,121,255)),(p['goal'],'G',(29,186,112))]:
        x,y=pt[0]*w,pt[1]*h;r=max(8,round(10*k))
        d.ellipse((x-r,y-r,x+r,y+r),fill=c,outline='white',width=max(2,round(2*k)))
        d.text((min(x+16*k,w-38*k),max(3,y-19*k)),label,font=font(round(23*k),True),fill=c,stroke_width=2,stroke_fill='white')
    if p['decision']=='wait':
        x,y=p['start'][0]*w,p['start'][1]*h;r=round(26*k)
        d.ellipse((x-r,y-r,x+r,y+r),outline=AI,width=max(4,round(5*k)))
        box=(int(x-60*k),int(y-78*k),int(x+61*k),int(y-36*k))
        d.rounded_rectangle(box,radius=7,fill=INK,outline=AI,width=2)
        d.text((box[0]+10*k,box[1]+6*k),'WAIT',font=font(round(26*k),True),fill=AI)
    return im

def overlay(original,p,humans=None):
    im=original.copy().convert('RGB');w,h=im.size;k=w/1280
    if humans:
        lay=Image.new('RGBA',im.size,(0,0,0,0));dr=ImageDraw.Draw(lay)
        for a in humans:
            if a['decision']!='path' or a['excluded']:continue
            points=[(q[0]*w,q[1]*h) for q in a['curve_64']]
            if a['outlier']:draw_dashed(dr,points,(255,182,58,145),max(2,round(3*k)))
            else:dr.line(points,fill=(*HUMAN,190),width=max(2,round(3*k)),joint='curve')
        im=Image.alpha_composite(im.convert('RGBA'),lay).convert('RGB')
    if p['decision']=='path':
        dr=ImageDraw.Draw(im);points=[(q[0]*w,q[1]*h) for q in dense_curve(p)]
        dr.line(points,fill=(4,24,36),width=max(7,round(10*k)),joint='curve')
        dr.line(points,fill=AI,width=max(4,round(6*k)),joint='curve')
        # A small arrowhead indicates progress along the candidate, not velocity.
        idx=int(len(points)*.66);a=points[max(idx-3,0)];b=points[idx];theta=math.atan2(b[1]-a[1],b[0]-a[0]);size=12*k
        poly=[b,(b[0]-size*math.cos(theta-.6),b[1]-size*math.sin(theta-.6)),(b[0]-size*math.cos(theta+.6),b[1]-size*math.sin(theta+.6))]
        dr.polygon(poly,fill=AI)
    return markers(im,p)

def svg(p):
    w,h=p['width'],p['height'];parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">', '<title>Exploratory AI annotation; not physically validated</title>']
    if p['decision']=='path':
        points=p['points'];d=f'M {points[0][0]*w:.3f} {points[0][1]*h:.3f}'
        for i,s in enumerate(p['segments']):
            c1=s['c1'];c2=s['c2'];end=points[i+1]
            d+=f' C {c1[0]*w:.3f},{c1[1]*h:.3f} {c2[0]*w:.3f},{c2[1]*h:.3f} {end[0]*w:.3f},{end[1]*h:.3f}'
        parts.append(f'<path d="{d}" fill="none" stroke="#00dcf0" stroke-width="6" stroke-linecap="round"/>')
    else:
        x,y=p['start'][0]*w,p['start'][1]*h
        parts.append(f'<circle cx="{x}" cy="{y}" r="26" fill="none" stroke="#00dcf0" stroke-width="5"/><text x="{x-40}" y="{y-38}" fill="#00dcf0" font-size="26">WAIT</text>')
    parts.append('</svg>');return '\n'.join(parts)

def render_all(source,root):
    obj=json.loads((root/'data/ai_proposals_with_samples.json').read_text());hd=json.loads((root/'data/human_reference.json').read_text())
    with (root/'data/scene_metrics.csv').open() as f:metrics={r['image_id']:r for r in csv.DictReader(f)}
    reviewfiles=[]
    for p in obj['proposals']:
        folder=root/'images'/p['folder'];folder.mkdir(parents=True,exist_ok=True)
        image_data=next(x for x in hd['images'] if x['id']==p['image_id'])
        original=Image.open(source/'images'/p['folder']/image_data['original_file']).convert('RGB')
        if original.size!=(p['width'],p['height']):raise ValueError('Original dimensions disagree')
        humans=[a for a in hd['annotations'] if a['image_id']==p['image_id']]
        own=overlay(original,p);cmp=overlay(original,p,humans)
        own.save(folder/'ai_path.jpg',quality=95,subsampling=0)
        cmp.save(folder/'comparison.jpg',quality=94,subsampling=0)
        (folder/'ai_overlay.svg').write_text(svg(p))
        w,h=cmp.size;k=w/1280;m=metrics[p['image_id']]
        f=font(round(21*k));b=font(round(29*k),True);small=font(round(18*k))
        head=round(116*k);footer=round(220*k)
        can=Image.new('RGB',(w,h+head+footer),PAPER);dr=ImageDraw.Draw(can)
        dr.rectangle((0,0,w,head),fill=INK)
        title=f"{p['index']:02d} | AI {p['decision'].upper()} | {p['image_title']}"
        for j,line in enumerate(wrap(dr,title,w-50*k,b)[:2]):dr.text((25*k,15*k+j*35*k),line,font=b,fill='white')
        dr.text((25*k,head-32*k),'Cyan: AI proposal   Amber: human paths   Dashed amber: export-flagged outlier',font=small,fill='white')
        can.paste(cmp,(0,head));y=head+h+16*k
        if p['decision']=='path':
            metric=f"Mean AI-human Frechet: {float(m['ai_mean_frechet'])*100:.2f}% of diagonal | Human paths: {m['n_human_paths']} | Human waits: {m['n_human_wait']}"
        else:metric=f"No movement curve; geometric distance is N/A | Human paths: {m['n_human_paths']} | Human waits: {m['n_human_wait']}"
        for text in [metric,p['rationale'],'Review: '+p['required_review']]:
            for line in wrap(dr,text,w-50*k,f):dr.text((25*k,y),line,font=f,fill=INK);y+=27*k
            y+=5*k
        dr.text((25*k,can.height-28*k),'Reference-conditioned pilot. Still-image suggestion only; NOT a verified safe robot trajectory.',font=small,fill=(106,60,40))
        can.save(folder/'review.jpg',quality=93)
        reviewfiles.append(folder/'review.jpg')
    # Thumbnail contact sheets, all scenes and all decisions.
    for batch in range(0,len(obj['proposals']),9):
        group=obj['proposals'][batch:batch+9];tilew=520;tileh=395
        can=Image.new('RGB',(tilew*3,tileh*math.ceil(len(group)/3)+70),PAPER);dr=ImageDraw.Draw(can)
        dr.text((18,15),f'TrajLab | AI-human review | scenes {batch+1:02d}-{batch+len(group):02d}',font=font(28,True),fill=INK)
        for j,p in enumerate(group):
            im=Image.open(root/'images'/p['folder']/'comparison.jpg');thumb=ImageOps.contain(im,(500,300))
            x=(j%3)*tilew;y=70+(j//3)*tileh
            can.paste(thumb,(x+(tilew-thumb.width)//2,y+(300-thumb.height)//2))
            dr.text((x+12,y+304),f"{p['index']:02d}  AI {p['decision'].upper()}  |  {p['folder'][-6:]}",font=font(22,True),fill=INK)
            mm=metrics[p['image_id']]
            sub='Distance N/A for wait' if p['decision']=='wait' else f"Mean distance: {100*float(mm['ai_mean_frechet']):.2f}% diagonal"
            dr.text((x+12,y+337),sub,font=font(19),fill=INK)
            dr.text((x+12,y+365),f"Humans: {mm['n_human_paths']} paths, {mm['n_human_wait']} waits",font=font(18),fill=INK)
        can.save(root/'figures'/f'contact_sheet_{batch//9+1:02d}.jpg',quality=93)
    # Four illustrative scenes, not a cherry-picked quantitative evaluation subset.
    chosen=[12,25,17,38];can=Image.new('RGB',(1540,1130),PAPER);dr=ImageDraw.Draw(can)
    dr.text((20,16),'Example annotations | cyan: AI, amber: human drawings',font=font(31,True),fill=INK)
    for j,idx in enumerate(chosen):
        p=next(p for p in obj['proposals'] if p['index']==idx);im=Image.open(root/'images'/p['folder']/'comparison.jpg');im=ImageOps.contain(im,(750,420))
        x=10+(j%2)*770;y=72+(j//2)*525;can.paste(im,(x+(750-im.width)//2,y+(420-im.height)//2))
        dr.text((x+8,y+424),f"{idx:02d}  {p['decision'].upper()} | {p['image_title'][:42]}",font=font(23,True),fill=INK)
        captions={12:'Outside of a standing group; residual personal-space cost.',25:'Left detour; grass access and pedestrian timing need review.',17:'WAIT: do not assume a path through the visible bunting.',38:'Leave existing grass before turning around the right group.'}
        for ln,line in enumerate(wrap(dr,captions[idx],740,font(20))):dr.text((x+8,y+457+ln*24),line,font=font(20),fill=INK)
    can.save(root/'figures/example_annotations.jpg',quality=95)
    print(f'Rendered {len(reviewfiles)} scene reviews and 39 full-resolution AI-only overlays; every source image retained its dimensions.')
    return reviewfiles

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);a=ap.parse_args();render_all(a.source,a.root)
