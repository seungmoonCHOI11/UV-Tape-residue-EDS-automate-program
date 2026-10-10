"""Application report renderer: shared layout for editable PPTX and vector PDF.
Original images are embedded with contain-fit, never cropped or enhanced.
"""
from pathlib import Path
from collections import defaultdict
import csv, json, math, hashlib, zipfile
from io import BytesIO
from datetime import datetime, timezone
from PIL import Image
from .research import *
from .classification import feature_ratios

NAVY='#20334D'; MUTED='#687A91'; BLUE='#526BB5'; RED='#C15B64'; GREEN='#398E83'; AMBER='#D6A64E'; LIGHT='#F1F4F8'
def textnum(v):return '-' if v is None else f'{v:.1f}'
def percent(v):return textnum(v)+'%' if v is not None else '-'
def fit(iw,ih,x,y,w,h):
    scale=min(w/iw,h/ih);nw,nh=iw*scale,ih*scale
    return x+(w-nw)/2,y+(h-nh)/2,nw,nh

class Document:
    W,H=13.333,7.5
    def __init__(self,fmt,out):
        self.fmt,self.out=fmt,Path(out);self.number=0
        if fmt=='pptx':
            from pptx import Presentation
            from pptx.util import Inches
            self.doc=Presentation();self.doc.slide_width=Inches(self.W);self.doc.slide_height=Inches(self.H)
        else:
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
            self.doc=canvas.Canvas(str(out),pagesize=(self.W*72,self.H*72),pageCompression=1)
            self.doc.setTitle('UV Tape Residue Research')
    def page(self,title,subtitle=''):
        if self.number and self.fmt=='pdf':self.doc.showPage()
        self.number+=1
        if self.fmt=='pptx':self.slide=self.doc.slides.add_slide(self.doc.slide_layouts[6])
        self.text(.35,.22,12.5,.4,title,22,bold=True)
        self.text(.35,.72,12.5,.35,subtitle,9,color=MUTED)
        self.rect(.35,1.1,12.6,.015,BLUE)
        self.text(.35,7.15,11.9,.16,'SEM / EDS screening | C >= 2.40x and O >= 3.00x | Source origin not chemically confirmed by C/O alone',7,color=MUTED)
        self.text(12.35,7.13,.6,.18,str(self.number),8,color=MUTED)
    def rect(self,x,y,w,h,color=LIGHT):
        if self.fmt=='pptx':
            from pptx.util import Inches
            from pptx.dml.color import RGBColor
            s=self.slide.shapes.add_shape(1,Inches(x),Inches(y),Inches(w),Inches(h));s.fill.solid();s.fill.fore_color.rgb=RGBColor.from_string(color.lstrip('#'));s.line.fill.background()
        else:
            from reportlab.lib.colors import HexColor
            self.doc.setFillColor(HexColor(color));self.doc.rect(x*72,(self.H-y-h)*72,w*72,h*72,stroke=0,fill=1)
    def text(self,x,y,w,h,value,size=12,color=NAVY,bold=False):
        value=str(value)
        if self.fmt=='pptx':
            from pptx.util import Inches,Pt
            from pptx.dml.color import RGBColor
            s=self.slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h));tf=s.text_frame;tf.word_wrap=True;tf.margin_left=tf.margin_right=0;tf.margin_top=tf.margin_bottom=0
            for i,line in enumerate(value.split('\n')):
                p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.text=line;p.font.name='Arial';p.font.size=Pt(size);p.font.bold=bold;p.font.color.rgb=RGBColor.from_string(color.lstrip('#'))
        else:
            from reportlab.lib.colors import HexColor
            from reportlab.pdfbase.pdfmetrics import stringWidth
            font='HYSMyeongJo-Medium' if any(ord(c)>255 for c in value) else 'Helvetica-Bold' if bold else 'Helvetica'
            self.doc.setFillColor(HexColor(color));self.doc.setFont(font,size)
            yy=(self.H-y)*72-size
            for paragraph in value.split('\n'):
                line=''
                for char in paragraph:
                    if stringWidth(line+char,font,size)>w*72 and line:
                        self.doc.drawString(x*72,yy,line);yy-=size*1.25;line=''
                    line+=char
                self.doc.drawString(x*72,yy,line);yy-=size*1.25
    def image(self,path,x,y,w,h,polygons=None):
        if not path:
            self.rect(x,y,w,h,LIGHT);self.text(x+.1,y+h/2-.15,w-.2,.4,'Image unavailable',11,color=MUTED);return
        with Image.open(path) as im:iw,ih=im.size
        xx,yy,ww,hh=fit(iw,ih,x,y,w,h)
        if self.fmt=='pptx':
            from pptx.util import Inches
            self.slide.shapes.add_picture(str(path),Inches(xx),Inches(yy),width=Inches(ww),height=Inches(hh))
        else:self.doc.drawImage(str(path),xx*72,(self.H-yy-hh)*72,ww*72,hh*72,preserveAspectRatio=True,mask='auto')
        if polygons:
            if isinstance(polygons[0],dict):polygons=[polygons]
            for poly in polygons:
                pts=[]
                for p in poly if isinstance(poly,list) else []:
                    try:
                        px,py=(float(p['x']),float(p['y'])) if isinstance(p,dict) else (float(p[0]),float(p[1]))
                        if math.isfinite(px) and math.isfinite(py):pts.append((xx+max(0,min(1,px))*ww,yy+max(0,min(.92,py))*hh))
                    except (TypeError,ValueError,KeyError,IndexError):pass
                if len(pts)<3:continue
                if self.fmt=='pptx':
                    from pptx.util import Inches,Pt
                    from pptx.dml.color import RGBColor
                    from pptx.enum.shapes import MSO_CONNECTOR
                    for a,b in zip(pts,pts[1:]+pts[:1]):
                        s=self.slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(a[0]),Inches(a[1]),Inches(b[0]),Inches(b[1]));s.line.color.rgb=RGBColor(220,45,55);s.line.width=Pt(.8)
                else:
                    self.doc.setStrokeColorRGB(.86,.18,.22);self.doc.setLineWidth(.8);p=self.doc.beginPath();p.moveTo(pts[0][0]*72,(self.H-pts[0][1])*72)
                    for a,b in pts[1:]:p.lineTo(a*72,(self.H-b)*72)
                    p.close();self.doc.drawPath(p)
    def table(self,headers,rows,x,y,widths,row_h=.56,size=11):
        for row_i,row in enumerate([headers]+rows):
            xx=x
            for j,value in enumerate(row):
                self.rect(xx,y+row_i*row_h,widths[j]-.015,row_h-.015,LIGHT if row_i==0 else '#FAFBFD' if row_i%2 else '#FFFFFF')
                self.text(xx+.09,y+row_i*row_h+.1,widths[j]-.18,row_h-.12,value,size if row_i else size-1,bold=row_i==0);xx+=widths[j]
    def save(self):
        if self.fmt=='pptx':self.doc.save(self.out)
        else:self.doc.save()
        return self.out

class Assets:
    """Load one point at a time. No fallback from raw SEM to an overlay."""
    def __init__(self,folder,storage=None):self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.storage=storage;self.missing=[];self.hashes={}
    def get(self,p,key):
        rid=str(p.get('id'));asset=(p.get('assets') or {}).get(key);path=None
        if isinstance(asset,str) and Path(asset).is_file():path=Path(asset)
        r2key=(p.get('r2_assets') or {}).get(key)
        if not r2key and isinstance(asset,str) and not asset.startswith(('/','http')):r2key=asset
        if path is None and r2key and self.storage is not None and self.storage.configured:
            path=self.folder/(hashlib.sha256((rid+key).encode()).hexdigest()+'.jpg')
            try:
                if not path.exists():self.storage.download_file(r2key,path)
            except Exception:path=None
        if path:
            try:
                with Image.open(path) as im:im.verify()
                if key=='sem':self.hashes[rid]=hashlib.sha256(path.read_bytes()).hexdigest()
                return path
            except Exception:pass
        self.missing.append({'point_id':rid,'asset':key});return None
    def clear_downloads(self):
        for p in self.folder.glob('*.jpg'):p.unlink(missing_ok=True)

def metadata(p,basis):
    c,o,src=feature_ratios(p.get('features') or {})
    return f"{condition(p)} | W{p.get('wafer')} {POSITIONS.get(integer(p.get('wafer')),'Other')} | P{p.get('point')} | {substrate(p)}",c,o,src

def point_page(doc,p,opts,assets):
    line,c,o,src=metadata(p,opts.cohort.basis)
    doc.page(f"{condition(p)}   W{p.get('wafer')} / P{p.get('point')}",f"{substrate(p)} | {POSITIONS.get(integer(p.get('wafer')),'Other')} | {decision(p,opts.cohort.basis)} | {opts.cohort.basis.upper()} decision basis")
    f=p.get('features') or {};polygons=(f.get('human_roi_polygons') or f.get('human_roi_polygon')) if opts.include_verification else None
    panels=[(.35,1.35,4.6,3.08,'sem','SEM original'+(' + saved Human ROI' if polygons else '')),(5.12,1.35,3.6,3.08,'eds_map','Full EDS original'),(8.9,1.35,4.05,5.32,'full_element_maps_original','Element maps original'),(.35,4.68,2.2,1.85,'c_map','C map'+(' + ROI' if polygons else '')),(2.75,4.68,2.2,1.85,'o_map','O map'+(' + ROI' if polygons else ''))]
    for x,y,w,h,key,title in panels:
        doc.text(x,y-.2,w,.2,title,10,bold=True);doc.image(assets.get(p,key),x,y,w,h,polygons if key in ('sem','c_map','o_map') else None)
    doc.rect(5.12,4.48,3.6,2.2,LIGHT)
    lines=[f"Result: {decision(p,opts.cohort.basis)}",f"C ROI/Global: {textnum(c)}x",f"O ROI/Global: {textnum(o)}x",f"Score: {textnum(score(p))} / 100"]
    if opts.include_verification:lines += [f"Human: {label(p.get('human_result')) or 'Not verified'}",f"Ratio source: {src}",f"Reviewed: {p.get('human_verified_at') or p.get('human_updated_at') or '-'}"]
    doc.text(5.28,4.65,3.26,1.85,'\n'.join(lines),10)
    doc.text(.35,6.78,12.5,.22,f"Point ID: {p.get('id')} | Batch: {p.get('project_id') or 'Unknown'}",8,color=MUTED)

def gallery_page(doc,key,pts,opts,assets,start,total):
    sub,res,cond,b=key
    band_label={'0-60':'0 <= score < 60','60-70':'60 <= score < 70','70-80':'70 <= score < 80','80-90':'80 <= score < 90','90-100':'90 <= score <= 100','missing':'Score unavailable'}.get(b,b)
    doc.page(f"Original SEM | {res} | {band_label}",f"{sub} | {cond} | {start+1}-{start+len(pts)} of {total} | {opts.cohort.basis.upper()} decision basis | Original stored SEM, no ROI or enhancement")
    cols,rows={10:(5,2),12:(4,3),16:(4,4),20:(5,4)}[opts.per_page];cw=12.6/cols;ch=5.75/rows
    for i,p in enumerate(pts):
        x=.35+(i%cols)*cw;y=1.26+(i//cols)*ch
        doc.image(assets.get(p,'sem'),x+.045,y,cw-.14,ch-.56)
        doc.text(x+.045,y+ch-.52,cw-.13,.16,f"{p.get('power')} {p.get('time')} | W{p.get('wafer')} P{p.get('point')}",8,bold=True)
        doc.text(x+.045,y+ch-.34,cw-.13,.15,f"Score {textnum(score(p))} | {str(p.get('id',''))[:10]}",7,color=MUTED)

def summary_pages(doc,records,opts):
    data=summary(records,opts.cohort)
    doc.page(opts.title,'Condition / location evaluation | W1 Corner, W4 Edge, W5 Middle | W9 reference evaluated separately')
    doc.text(.4,1.45,12,1.0,'Plasma activation and UV tape residue screening',27,bold=True)
    doc.text(.4,2.5,11.9,1.1,'SEM morphology and C/O enrichment support a residue screening decision.\nResidue denotes the configured screening class; it is not source-specific chemical identification.',17,color=MUTED)
    st=stats(records,opts.cohort.basis)
    doc.table(['Selected points','Residue','Non-residue','Ambiguous','Human verified'],[[str(st[k]) for k in ['n','residue','non','ambiguous','verified']]],.4,3.9,[2.5]*5,row_h=.65,size=15)
    doc.text(.4,5.5,12,1.25,f"Scope: {opts.cohort.scope} | Decision basis: {opts.cohort.basis}\nRate = residue points / observed points. Ambiguous remains in denominator.\nPooled location rates reflect condition mix. Balanced rates equally weight common conditions.\nC/O score is a screening index, not probability, chemical concentration or removal efficiency.",12,color=MUTED)
    for s in data['strata']:
        for start in range(0,len(s['conditions']),5):
            rows=s['conditions'][start:start+5];ws=[c['wafer'] for c in rows[0]['cells']] if rows else []
            doc.page('Plasma condition comparison',f"Substrate: {s['substrate']} | {opts.cohort.scope} | {opts.cohort.basis.upper()} basis | Rate [Residue / n]")
            headers=['Condition']+[f"W{w} {POSITIONS.get(w,'Other')}" for w in ws]+['R / n','Amb.','Spread']
            widths=[2.3]+[7.0/max(1,len(ws))]*len(ws)+[1.15,.9,1.25]
            data_rows=[]
            for r in rows:data_rows.append([r['condition']]+[f"{percent(c['rate'])}\n{c['residue']} / {c['n']}" for c in r['cells']]+[f"{r['residue']}/{r['n']}",str(r['ambiguous']),textnum(r['spread'])+' pp' if r['spread'] is not None else '-'])
            doc.table(headers,data_rows,.35,1.38,widths,row_h=.72,size=12)
            doc.text(.4,6.3,12.3,.6,'Missing observations are shown as -. Spread requires all selected wafer positions.\nRepeated W/P labels are not assumed to be paired physical specimens.',10,color=MUTED)
        doc.page('Wafer position across plasma conditions',f"Substrate: {s['substrate']} | Common conditions: {len(s['common_keys'])} | All selected conditions pooled vs equal-condition weighting")
        doc.table(['Position','N','Residue / n','Pooled','Balanced','Ambiguous'],[[f"W{p['wafer']} {p['label']}",str(p['n']),f"{p['residue']}/{p['n']}",percent(p['rate']),percent(p['balanced']),str(p['ambiguous'])] for p in s['positions']],.35,1.35,[2.5,1,2,2,2,3.1],row_h=.65,size=13)
        y=1.35+.65*(len(s['positions'])+1)+.35
        if len(s['positions'])<=4:
            for i,p in enumerate(s['positions']):
                yy=y+i*.42;doc.text(.45,yy,2,.2,f"W{p['wafer']} {p['label']}",11);doc.rect(2.8,yy,7,.17,LIGHT)
                if p['balanced'] is not None:doc.rect(2.8,yy,7*p['balanced']/100,.17,BLUE)
                doc.text(10.05,yy,2,.2,percent(p['balanced']),11)
        doc.text(.4,6.45,12.3,.4,'Balanced = mean of each common condition rate at this position. This is descriptive standardization, not a causal effect.',10,color=MUTED)

def render_export(records,opts,folder,storage=None,progress=lambda **x:None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);assets=Assets(folder/'assets',storage)
    total=page_count(records,opts);outputs=[];manifest=[];done=0;max_pages=25 if opts.kind=='points' else 10
    doc=None;part=0
    def page_ready():
        nonlocal doc,part
        if doc is None:
            part+=1;doc=Document(opts.format,folder/f'{opts.kind}_{part:03d}.{opts.format}')
    def flush():
        nonlocal doc
        if doc is not None:outputs.append(doc.save());doc=None;assets.clear_downloads()
    if opts.kind=='summary':
        page_ready();summary_pages(doc,records,opts);done=total;flush()
    elif opts.kind=='points':
        for p in records:
            page_ready();point_page(doc,p,opts,assets);done+=1;progress(progress=round(done/total*95),message=f'Point {done}/{total}')
            manifest.append({'point_id':p['id'],'file':doc.out.name,'page':doc.number})
            if doc.number>=max_pages:flush()
        flush()
    else:
        for key,pts in gallery_groups(records,opts):
            for start in range(0,len(pts),opts.per_page):
                page_ready();a=pts[start:start+opts.per_page];gallery_page(doc,key,a,opts,assets,start,len(pts));done+=1
                for p in a:manifest.append({'point_id':p['id'],'file':doc.out.name,'page':doc.number,'group':list(key)})
                progress(progress=round(done/total*95),message=f'SEM gallery {done}/{total}')
                if doc.number>=max_pages:flush()
        flush()
    # Always deliver an audit manifest and original-SEM hashes alongside the outputs.
    audit={'created_at':datetime.now(timezone.utc).isoformat(),'options':opts.model_dump(),'point_ids':[p['id'] for p in records],'n':len(records),'pages':total,'rule':CLASSIFICATION_RULE_TEXT,'source_note':'Original means stored SEM extracted from source PDF. No overlay, enhancement or crop added in gallery.','missing_assets':assets.missing,'sem_sha256':assets.hashes,'page_index':manifest}
    (folder/'manifest.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    with (folder/'points.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['Point ID','Batch','Condition','Wafer','Position','Point','Substrate','Result','Human','C ratio','O ratio','Score','Score band'])
        for p in records:
            _,c,o,_=metadata(p,opts.cohort.basis)
            row=[p['id'],p.get('project_id'),condition(p),p.get('wafer'),POSITIONS.get(integer(p.get('wafer')),'Other'),p.get('point'),substrate(p),decision(p,opts.cohort.basis),label(p.get('human_result')) or '',c,o,score(p),band(p)]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in row])
    archive=folder/'report_bundle.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in outputs+[folder/'manifest.json',folder/'points.csv']:z.write(p,p.name)
    return {'file':archive.name,'n':len(records),'pages':total,'parts':len(outputs),'missing_assets':len(assets.missing)}
