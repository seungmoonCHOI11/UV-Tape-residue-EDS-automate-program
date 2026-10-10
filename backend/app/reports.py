from collections import defaultdict
from pathlib import Path
import re
import textwrap

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .classification import record_result, CLASSIFICATION_RULE_TEXT

NAVY=RGBColor(20,35,55)
BLUE=RGBColor(52,92,170)
LIGHT=RGBColor(241,245,249)
MID=RGBColor(220,227,236)
GREEN=RGBColor(33,135,95)
AMBER=RGBColor(181,123,18)
RED=RGBColor(192,68,78)

LOCATION_DEFS=((1,"Corner"),(4,"Edge"),(5,"Middle"))


def _add_box(slide, x,y,w,h,title):
    shape=slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.line.color.rgb=MID
    shape.fill.solid(); shape.fill.fore_color.rgb=RGBColor(255,255,255)
    tb=slide.shapes.add_textbox(Inches(x+.08), Inches(y+.05), Inches(w-.16), Inches(.2))
    tb.text_frame.paragraphs[0].text=title
    tb.text_frame.paragraphs[0].font.size=Pt(8); tb.text_frame.paragraphs[0].font.bold=True
    return shape


def _result(p):
    return record_result(p)


def _rate(n,d):
    return (100.0*n/d) if d else 0.0


def _num(v):
    m=re.search(r"-?\d+(?:\.\d+)?",str(v or ""))
    return float(m.group()) if m else 0.0


def _condition_key(p):
    return f"{p.get('power','-')} / {p.get('time','-')}"


def _group_conditions(records):
    groups=defaultdict(list)
    for p in records:
        groups[_condition_key(p)].append(p)
    return dict(sorted(groups.items(), key=lambda kv:(_num(kv[0].split('/')[0]),_num(kv[0].split('/')[1]))))


def engineering_summary(records):
    """Aggregate company-style engineering metrics without averaging the residue score."""
    rows=[]
    groups=_group_conditions(records)
    for key, all_points in groups.items():
        focus=[p for p in all_points if int(p.get("wafer") or 0) in (1,4,5)]
        location_rows=[]
        for wafer,label in LOCATION_DEFS:
            pts=[p for p in focus if int(p.get("wafer") or 0)==wafer]
            r=sum(_result(p)=="Residue" for p in pts)
            n=sum(_result(p)=="Non-residue" for p in pts)
            a=len(pts)-r-n
            location_rows.append({"wafer":wafer,"label":label,"points":len(pts),"residue":r,"non":n,"ambiguous":a,"residue_rate":_rate(r,len(pts)) if pts else None})
        r=sum(_result(p)=="Residue" for p in focus)
        n=sum(_result(p)=="Non-residue" for p in focus)
        a=len(focus)-r-n
        valid_rates=[x["residue_rate"] for x in location_rows if x["residue_rate"] is not None]
        spread=max(valid_rates)-min(valid_rates) if len(valid_rates)>=2 else None
        worst=max((x for x in location_rows if x["residue_rate"] is not None), key=lambda x:x["residue_rate"], default=None)
        verified=sum(bool(p.get("human_result")) for p in focus)
        rows.append({
            "condition":key,
            "power":key.split(" / ")[0],
            "time":key.split(" / ")[1],
            "points":len(focus),
            "residue":r,"non":n,"ambiguous":a,
            "residue_rate":_rate(r,len(focus)) if focus else 0.0,
            "ambiguous_rate":_rate(a,len(focus)) if focus else 0.0,
            "verified":verified,
            "coverage":min(100.0,_rate(len(focus),27)),
            "location_spread":spread,
            "worst_location":worst["label"] if worst else "-",
            "locations":location_rows,
            "excluded_other_wafers":len(all_points)-len(focus),
        })
    return rows




def _substrate(p):
    f=p.get("features") or {}
    value=p.get("substrate_type") or (f.get("substrate_type") if isinstance(f,dict) else None) or "SiCN"
    return str(value)


def reference_summary(records, wafer=9):
    """Keep W9/reference substrates separate from the W1/W4/W5 MAIN ranking."""
    groups=defaultdict(list)
    for p in records:
        if int(p.get("wafer") or 0)!=int(wafer):
            continue
        groups[(_condition_key(p),_substrate(p))].append(p)
    rows=[]
    for (condition,substrate),pts in groups.items():
        r=sum(_result(p)=="Residue" for p in pts)
        n=sum(_result(p)=="Non-residue" for p in pts)
        a=len(pts)-r-n
        rows.append({"condition":condition,"substrate":substrate,"points":len(pts),"residue":r,"non":n,"ambiguous":a,"residue_rate":_rate(r,len(pts)) if pts else 0.0,"ambiguous_rate":_rate(a,len(pts)) if pts else 0.0,"verified":sum(bool(p.get("human_result")) for p in pts)})
    return sorted(rows,key=lambda x:(x["substrate"],_num(x["condition"].split("/")[0]),_num(x["condition"].split("/")[1])))

def _ppt_title(slide,title,subtitle=None):
    tb=slide.shapes.add_textbox(Inches(.42),Inches(.25),Inches(12.45),Inches(.45))
    p=tb.text_frame.paragraphs[0];p.text=title;p.font.size=Pt(23);p.font.bold=True;p.font.color.rgb=NAVY
    if subtitle:
        sb=slide.shapes.add_textbox(Inches(.44),Inches(.73),Inches(12.2),Inches(.28))
        q=sb.text_frame.paragraphs[0];q.text=subtitle;q.font.size=Pt(9);q.font.color.rgb=RGBColor(105,118,135)
    line=slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(.42),Inches(1.08),Inches(12.45),Inches(.025))
    line.line.fill.background();line.fill.solid();line.fill.fore_color.rgb=BLUE


def _ppt_table(slide, data, x,y,w,h, widths=None, font_size=9, header_fill=LIGHT):
    rows=len(data);cols=len(data[0]) if rows else 0
    table=slide.shapes.add_table(rows,cols,Inches(x),Inches(y),Inches(w),Inches(h)).table
    if widths:
        for i,val in enumerate(widths): table.columns[i].width=Inches(val)
    for r,row in enumerate(data):
        for c,val in enumerate(row):
            cell=table.cell(r,c);cell.text=str(val)
            cell.margin_left=Inches(.05);cell.margin_right=Inches(.05);cell.margin_top=Inches(.035);cell.margin_bottom=Inches(.035)
            cell.fill.solid();cell.fill.fore_color.rgb=header_fill if r==0 else RGBColor(255,255,255)
            p=cell.text_frame.paragraphs[0];p.font.size=Pt(font_size if r else font_size-1);p.font.bold=(r==0);p.font.color.rgb=NAVY;p.alignment=PP_ALIGN.CENTER
    return table


def _location_text(loc):
    if not loc or not loc.get("points"): return "-"
    return f"{loc['residue']}/{loc['points']} ({loc['residue_rate']:.1f}%)"


def export_engineering_ppt(records,out):
    rows=engineering_summary(records)
    refs=reference_summary(records,9)
    prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5);blank=prs.slide_layouts[6]

    # Slide 1: executive condition comparison
    s=prs.slides.add_slide(blank)
    _ppt_title(s,"UV Tape Residue - Engineering Condition Summary","Primary KPI: residue incidence (lower is better) | Focus locations: W1 Corner, W4 Edge, W5 Middle")
    if rows:
        best=min(rows,key=lambda x:(x["residue_rate"],x["ambiguous_rate"]))
        worstspread=max((x for x in rows if x["location_spread"] is not None),key=lambda x:x["location_spread"],default=None)
        cards=[("Conditions",str(len(rows)),"Power / Time splits"),("Lowest residue",best["condition"],f"{best['residue']}/{best['points']} = {best['residue_rate']:.1f}%"),("Location sensitivity",worstspread["condition"] if worstspread else "-",f"Max-min {worstspread['location_spread']:.1f}%p" if worstspread else "Insufficient data")]
        for i,(title,val,note) in enumerate(cards):
            x=.45+i*4.13
            box=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(x),Inches(1.35),Inches(3.82),Inches(1.12));box.fill.solid();box.fill.fore_color.rgb=RGBColor(249,251,253);box.line.color.rgb=MID
            t=s.shapes.add_textbox(Inches(x+.16),Inches(1.5),Inches(3.5),Inches(.8));tf=t.text_frame
            p=tf.paragraphs[0];p.text=title;p.font.size=Pt(8);p.font.bold=True;p.font.color.rgb=RGBColor(112,124,139)
            p=tf.add_paragraph();p.text=val;p.font.size=Pt(15 if len(val)<18 else 11);p.font.bold=True;p.font.color.rgb=NAVY
            p=tf.add_paragraph();p.text=note;p.font.size=Pt(8);p.font.color.rgb=RGBColor(105,118,135)
        data=[["Condition","N","Residue n (%)","Non n (%)","Ambiguous n (%)","Location spread","Worst location","Coverage"]]
        for x in rows:
            data.append([x["condition"],x["points"],f"{x['residue']} ({x['residue_rate']:.1f}%)",f"{x['non']} ({_rate(x['non'],x['points']):.1f}%)" if x['points'] else "-",f"{x['ambiguous']} ({x['ambiguous_rate']:.1f}%)",f"{x['location_spread']:.1f}%p" if x['location_spread'] is not None else "-",x["worst_location"],f"{x['coverage']:.0f}%"])
        _ppt_table(s,data,.45,2.75,12.35,3.95,font_size=8)
    else:
        _ppt_title(s,"UV Tape Residue - Engineering Condition Summary","No records available")

    # Slide 2: Power x Time matrix
    s=prs.slides.add_slide(blank);_ppt_title(s,"Plasma Condition Comparison","Residue incidence by Power x Time; each cell shows Residue/Total and rate")
    powers=sorted({x["power"] for x in rows},key=_num);times=sorted({x["time"] for x in rows},key=_num)
    lookup={(x["power"],x["time"]):x for x in rows}
    data=[["Power \\ Time"]+times]
    for pwr in powers:
        row=[pwr]
        for tm in times:
            x=lookup.get((pwr,tm));row.append(f"{x['residue']}/{x['points']}\n{x['residue_rate']:.1f}%" if x else "-")
        data.append(row)
    if len(data)>1:_ppt_table(s,data,.55,1.45,12.2,min(5.3,.68*len(data)),font_size=10)
    note=s.shapes.add_textbox(Inches(.58),Inches(6.55),Inches(12),Inches(.45));note.text_frame.paragraphs[0].text="Use this matrix to compare power/time splits directly. Score averages are intentionally excluded.";note.text_frame.paragraphs[0].font.size=Pt(9);note.text_frame.paragraphs[0].font.color.rgb=RGBColor(100,112,128)

    # Slide 3: wafer position
    s=prs.slides.add_slide(blank);_ppt_title(s,"Wafer Position Comparison","W1 = Corner | W4 = Edge | W5 = Middle; lower residue incidence and smaller spread indicate better observed uniformity")
    data=[["Condition","W1 Corner","W4 Edge","W5 Middle","Max-min spread","Worst location","Human verified"]]
    for x in rows:
        lm={z["wafer"]:z for z in x["locations"]}
        data.append([x["condition"],_location_text(lm.get(1)),_location_text(lm.get(4)),_location_text(lm.get(5)),f"{x['location_spread']:.1f}%p" if x['location_spread'] is not None else "-",x["worst_location"],f"{x['verified']}/{x['points']}"])
    if len(data)>1:_ppt_table(s,data,.45,1.45,12.35,min(5.3,.6*len(data)+.5),font_size=8)

    # Slide 4: data quality and rule
    s=prs.slides.add_slide(blank);_ppt_title(s,"Evaluation Basis & Data Quality","The report uses classification counts, spatial dependence, and completeness - not average residue scores")
    bullets=[
        "Final result priority: Human Verified label > current C/O rule > legacy stored label only when ratio data is unavailable.",
        "Current automatic rule: "+CLASSIFICATION_RULE_TEXT,
        "Primary comparison: Residue incidence = Residue points / measured points.",
        "Spatial robustness: compare W1 Corner, W4 Edge, W5 Middle and report max-min residue-rate spread.",
        "Ambiguous points are reported separately and are not silently counted as Non-residue.",
        "Coverage assumes the MAIN engineering set is W1/W4/W5 x P1-P9 = 27 points per condition; W9 is excluded.",
        "W9/reference substrates (for example Si) are compared only against the same W9 substrate group and never mixed into MAIN ranking.",
    ]
    y=1.5
    for b in bullets:
        box=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(.7),Inches(y),Inches(11.95),Inches(.62));box.fill.solid();box.fill.fore_color.rgb=RGBColor(249,251,253);box.line.color.rgb=MID
        tx=s.shapes.add_textbox(Inches(.9),Inches(y+.13),Inches(11.55),Inches(.35));p=tx.text_frame.paragraphs[0];p.text=b;p.font.size=Pt(10);p.font.color.rgb=NAVY
        y+=.82
    # Slide 5: W9 reference datasets, separated by substrate
    s=prs.slides.add_slide(blank);_ppt_title(s,"W9 Reference / Other","Excluded from MAIN ranking; compare W9 datasets by substrate type (e.g., Si vs Si only)")
    if refs:
        data=[["Condition","W9 substrate","N","Residue n (%)","Non n (%)","Ambiguous n (%)","Human verified"]]
        for x in refs:
            data.append([x["condition"],x["substrate"],x["points"],f"{x['residue']} ({x['residue_rate']:.1f}%)",f"{x['non']} ({_rate(x['non'],x['points']):.1f}%)",f"{x['ambiguous']} ({x['ambiguous_rate']:.1f}%)",f"{x['verified']}/{x['points']}"])
        _ppt_table(s,data,.5,1.45,12.25,min(5.3,.58*len(data)+.5),font_size=8)
    else:
        note=s.shapes.add_textbox(Inches(.7),Inches(1.55),Inches(11.8),Inches(.8));p=note.text_frame.paragraphs[0];p.text="No W9 reference data yet. Future W9 Si uploads will appear here and will be compared only with W9 Si datasets.";p.font.size=Pt(12);p.font.color.rgb=NAVY

    prs.save(out);return out


def export_engineering_pdf(records,out):
    rows=engineering_summary(records)
    refs=reference_summary(records,9)
    c=canvas.Canvas(str(out),pagesize=landscape(A4));W,H=landscape(A4)
    def title(t,sub):
        c.setFillColorRGB(.08,.14,.22);c.setFont("Helvetica-Bold",18);c.drawString(28,H-32,t)
        c.setFillColorRGB(.38,.44,.52);c.setFont("Helvetica",7.5);c.drawString(28,H-46,sub)
        c.setStrokeColorRGB(.20,.36,.67);c.line(28,H-54,W-28,H-54)
    def table(data,x,y,col_widths,row_h=24,fs=7):
        yy=y
        for ri,row in enumerate(data):
            xx=x
            for ci,val in enumerate(row):
                cw=col_widths[ci]
                if ri==0:
                    c.setFillColorRGB(.94,.96,.98);c.rect(xx,yy-row_h,cw,row_h,fill=1,stroke=0)
                    c.setFont("Helvetica-Bold",fs)
                else:c.setFont("Helvetica",fs)
                c.setStrokeColorRGB(.84,.87,.91);c.rect(xx,yy-row_h,cw,row_h,fill=0,stroke=1)
                c.setFillColorRGB(.08,.14,.22)
                text=str(val).replace("\n"," ")
                c.drawCentredString(xx+cw/2,yy-row_h/2-2,text[:55])
                xx+=cw
            yy-=row_h
        return yy

    title("UV Tape Residue - Engineering Condition Summary","Primary KPI: residue incidence (lower is better) | W1 Corner / W4 Edge / W5 Middle")
    data=[["Condition","N","Residue","Non","Ambiguous","Spread","Worst","Coverage"]]
    for x in rows:data.append([x["condition"],x["points"],f"{x['residue']} ({x['residue_rate']:.1f}%)",f"{x['non']} ({_rate(x['non'],x['points']):.1f}%)" if x['points'] else "-",f"{x['ambiguous']} ({x['ambiguous_rate']:.1f}%)",f"{x['location_spread']:.1f}%p" if x['location_spread'] is not None else "-",x["worst_location"],f"{x['coverage']:.0f}%"])
    table(data,28,H-78,[126,36,78,78,82,66,70,58],row_h=25,fs=6.5)
    c.setFillColorRGB(.38,.44,.52);c.setFont("Helvetica",7);c.drawString(28,28,"Average score is intentionally excluded from condition evaluation.");c.showPage()

    title("Plasma Condition Comparison","Power x Time matrix - Residue/Total and residue incidence")
    powers=sorted({x["power"] for x in rows},key=_num);times=sorted({x["time"] for x in rows},key=_num);lookup={(x["power"],x["time"]):x for x in rows}
    data=[["Power / Time"]+times]
    for pwr in powers:data.append([pwr]+[(f"{lookup[(pwr,t)]['residue']}/{lookup[(pwr,t)]['points']} ({lookup[(pwr,t)]['residue_rate']:.1f}%)" if (pwr,t) in lookup else "-") for t in times])
    avail=W-56;col0=100;rest=(avail-col0)/max(1,len(times));table(data,28,H-82,[col0]+[rest]*len(times),row_h=34,fs=7);c.showPage()

    title("Wafer Position Comparison","W1 = Corner | W4 = Edge | W5 = Middle")
    data=[["Condition","W1 Corner","W4 Edge","W5 Middle","Spread","Worst location","Verified"]]
    for x in rows:
        lm={z["wafer"]:z for z in x["locations"]};data.append([x["condition"],_location_text(lm.get(1)),_location_text(lm.get(4)),_location_text(lm.get(5)),f"{x['location_spread']:.1f}%p" if x['location_spread'] is not None else "-",x["worst_location"],f"{x['verified']}/{x['points']}"])
    table(data,28,H-82,[130,105,105,105,75,85,65],row_h=28,fs=6.7)
    c.setFillColorRGB(.38,.44,.52);c.setFont("Helvetica",7);c.drawString(28,28,"Location spread = max residue rate - min residue rate among W1/W4/W5 with data.");c.showPage()

    title("Evaluation Basis & Data Quality","Company-style comparison uses incidence, spatial dependence, ambiguity, and data completeness")
    bullets=[
        "Final result priority: Human Verified > current C/O rule > legacy label only if ratio data is unavailable.",
        "Current rule: "+CLASSIFICATION_RULE_TEXT,
        "Residue incidence is the primary condition KPI. Ambiguous points are reported separately.",
        "Spatial robustness is evaluated using W1 Corner, W4 Edge, W5 Middle and the max-min spread.",
        "Planned MAIN complete set: 27 points per condition (W1/W4/W5 x P1-P9). W9 is excluded from MAIN coverage.",
        "W9/reference substrates are reported separately by substrate type and are never mixed into the MAIN condition rate.",
    ]
    y=H-92
    for b in bullets:
        lines=textwrap.wrap("- "+b,width=122)[:2]
        box_h=42 if len(lines)>1 else 32
        c.setFillColorRGB(.97,.98,.99);c.roundRect(42,y-box_h,W-84,box_h,5,fill=1,stroke=0)
        c.setFillColorRGB(.08,.14,.22);c.setFont("Helvetica",8.5)
        for li,line in enumerate(lines):
            c.drawString(55,y-21-li*11,line)
        y-=box_h+14
    c.showPage()
    title("W9 Reference / Other","Excluded from MAIN ranking; W9 datasets are separated by substrate type")
    data=[["Condition","W9 substrate","N","Residue","Non","Ambiguous","Verified"]]
    for x in refs:
        data.append([x["condition"],x["substrate"],x["points"],f"{x['residue']} ({x['residue_rate']:.1f}%)",f"{x['non']} ({_rate(x['non'],x['points']):.1f}%)",f"{x['ambiguous']} ({x['ambiguous_rate']:.1f}%)",f"{x['verified']}/{x['points']}"])
    if len(data)>1:
        table(data,28,H-82,[130,90,45,95,95,95,70],row_h=28,fs=6.7)
    else:
        c.setFillColorRGB(.08,.14,.22);c.setFont("Helvetica",10);c.drawString(42,H-98,"No W9 reference data yet. Future W9 Si data will be grouped here separately.")
    c.save();return out


def export_ppt(records, out):
    prs=Presentation(); prs.slide_width=Inches(13.333); prs.slide_height=Inches(7.5)
    blank=prs.slide_layouts[6]
    for r in records:
        s=prs.slides.add_slide(blank);p=r
        s.shapes.add_textbox(Inches(.25),Inches(.12),Inches(9),Inches(.3)).text_frame.paragraphs[0].text=f"{p['power']}_{p['time']}_W{p['wafer']}_P{p['point']}"
        for shp in s.shapes:
            if hasattr(shp,"text_frame") and shp.text:
                shp.text_frame.paragraphs[0].font.size=Pt(18); shp.text_frame.paragraphs[0].font.bold=True;shp.text_frame.paragraphs[0].font.color.rgb=RGBColor(14,41,69)
        subtitle=s.shapes.add_textbox(Inches(.25),Inches(.46),Inches(12),Inches(.2));subtitle.text_frame.paragraphs[0].text=f"{p['power']} / {p['time']} · W{p['wafer']} / P{p['point']} · {p['zone']} · {_result(p)}";subtitle.text_frame.paragraphs[0].font.size=Pt(8)
        for x,y,w,h,key,title in [(.25,.88,6,2.3,"sem","SEM"),(6.45,.88,6,2.3,"spectrum","EDS Spectrum"),(.25,3.36,6,2.3,"eds_map","EDS Map"),(6.45,3.36,6,2.3,"element_maps","Element Maps")]:
            _add_box(s,x,y,w,h,title);img=p.get("assets",{}).get(key)
            if img and Path(img).exists():s.shapes.add_picture(img, Inches(x+.15), Inches(y+.3), width=Inches(w-.3), height=Inches(h-.45))
        _add_box(s,.25,5.84,7.7,1.25,"EDS / Element Data");t=s.shapes.add_textbox(Inches(.4),Inches(6.18),Inches(7.3),Inches(.65));f=p.get("features",{});t.text_frame.paragraphs[0].text=f"C enrichment {f.get('c_enrichment',0)}%   O enrichment {f.get('o_enrichment',0)}%\nC coverage {f.get('c_coverage',0)}%   O coverage {f.get('o_coverage',0)}%   Wafer W{p['wafer']}   Point P{p['point']}";t.text_frame.paragraphs[0].font.size=Pt(9)
        _add_box(s,8.15,5.84,4.3,1.25,"Result");t=s.shapes.add_textbox(Inches(8.35),Inches(6.3),Inches(3.9),Inches(.45));t.text_frame.paragraphs[0].text=_result(p);t.text_frame.paragraphs[0].font.size=Pt(21);t.text_frame.paragraphs[0].font.bold=True;t.text_frame.paragraphs[0].alignment=2
    prs.save(out);return out


def export_pdf(records,out):
    c=canvas.Canvas(str(out),pagesize=landscape(A4)); W,H=landscape(A4)
    for idx,p in enumerate(records):
        c.setFont("Helvetica-Bold",15); c.setFillColorRGB(.05,.16,.27);c.drawString(22,H-25,f"{p['power']}_{p['time']}_W{p['wafer']}_P{p['point']}")
        c.setFont("Helvetica",7); c.drawString(22,H-37,f"{p['power']} / {p['time']} · W{p['wafer']} / P{p['point']} · {p['zone']}");c.setStrokeColorRGB(.2,.36,.51); c.line(22,H-45,W-22,H-45)
        boxes=[(22,H-215,330,155,"SEM","sem"),(365,H-215,330,155,"EDS Spectrum","spectrum"),(22,H-380,330,155,"EDS Map","eds_map"),(365,H-380,330,155,"Element Maps","element_maps")]
        for x,y,w,h,title,key in boxes:
            c.setStrokeColorRGB(.82,.86,.89); c.rect(x,y,w,h);c.setFillColorRGB(.05,.18,.28); c.setFont("Helvetica-Bold",7); c.drawString(x+7,y+h-12,title);img=p.get("assets",{}).get(key)
            if img and Path(img).exists():c.drawImage(ImageReader(img),x+7,y+7,w-14,h-24,preserveAspectRatio=True,anchor='c')
        c.rect(22,52,420,70); c.rect(452,52,283,70);c.setFillColorRGB(.05,.1,.15); c.setFont("Helvetica-Bold",7); c.drawString(28,108,"EDS / Element Data");f=p.get("features",{});c.setFont("Helvetica",7); c.drawString(28,92,f"C enrichment: {f.get('c_enrichment',0)}%   O enrichment: {f.get('o_enrichment',0)}%");c.drawString(28,78,f"C coverage: {f.get('c_coverage',0)}%   O coverage: {f.get('o_coverage',0)}%   W{p['wafer']} / P{p['point']}");c.setFont("Helvetica-Bold",7); c.drawString(460,108,"Result");c.setFont("Helvetica-Bold",18); c.drawCentredString(593,76,_result(p));c.setFont("Helvetica",6); c.drawRightString(W-25,20,f"{idx+1} / {len(records)}");c.showPage()
    c.save(); return out
