"""Merge bounded render batches without decoding images or rasterizing slides."""
import posixpath
import shutil
import zipfile
from pathlib import Path
from lxml import etree as ET

P='http://schemas.openxmlformats.org/presentationml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
CT='http://schemas.openxmlformats.org/package/2006/content-types'

def _xml(node):return ET.tostring(node,xml_declaration=True,encoding='UTF-8',standalone=True)

def merge_documents(parts,target,fmt):
    parts=list(map(Path,parts));target=Path(target)
    if not parts:raise ValueError('No report pages to merge')
    if len(parts)==1:
        shutil.copyfile(parts[0],target);return
    if fmt=='pdf':
        # Never build a hundreds-of-page image PDF in one PyMuPDF Document.
        # Start from part 1, then append each small part and save incrementally.
        # This keeps resident memory nearly independent of total Point count.
        import fitz
        shutil.copyfile(parts[0],target)
        for path in parts[1:]:
            with fitz.open(target) as merged, fitz.open(path) as source:
                merged.insert_pdf(source)
                merged.saveIncr()
        return
    merge_pptx(parts,target)

def merge_pptx(parts,target):
    # Each batch gets its own OPC subtree, retaining relative relationships,
    # layouts, masters, themes, media and editable shapes. ZIP copies are streamed.
    with zipfile.ZipFile(parts[0]) as first:
        pres=ET.fromstring(first.read('ppt/presentation.xml'))
        rels=ET.fromstring(first.read('ppt/_rels/presentation.xml.rels'))
        types=ET.fromstring(first.read('[Content_Types].xml'))
        app=ET.fromstring(first.read('docProps/app.xml')) if 'docProps/app.xml' in first.namelist() else None
    slides=pres.find(f'{{{P}}}sldIdLst');masters=pres.find(f'{{{P}}}sldMasterIdLst')
    if slides is None:slides=ET.SubElement(pres,f'{{{P}}}sldIdLst')
    next_slide=max([255]+[int(x.get('id')) for x in slides])+1
    next_master=max([2147483647]+[int(x.get('id')) for x in masters])+1
    next_rel=1;used={x.get('Id') for x in rels}
    overrides={x.get('PartName') for x in types if x.tag==f'{{{CT}}}Override'}
    defaults={x.get('Extension') for x in types if x.tag==f'{{{CT}}}Default'}
    def relationship(kind,target):
        nonlocal next_rel
        while f'rIdMerged{next_rel}' in used:next_rel+=1
        rid=f'rIdMerged{next_rel}';used.add(rid);next_rel+=1
        ET.SubElement(rels,f'{{{REL}}}Relationship',Id=rid,Type=kind,Target=target)
        return rid
    def copy_entry(src,dst,info,name,relocate=False,prefix=''):
        # Absolute internal targets must move with their batch too.
        if relocate and name.endswith('.rels'):
            node=ET.fromstring(src.read(info.filename))
            for rel in node:
                target=rel.get('Target','')
                if rel.get('TargetMode')!='External' and target.startswith('/'):
                    rel.set('Target','/'+prefix+target.lstrip('/'))
            dst.writestr(name,_xml(node));return
        with src.open(info) as inp,dst.open(name,'w') as out:shutil.copyfileobj(inp,out,1024*1024)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as out:
        with zipfile.ZipFile(parts[0]) as src:
            for info in src.infolist():
                if info.filename not in ('ppt/presentation.xml','ppt/_rels/presentation.xml.rels','[Content_Types].xml','docProps/app.xml'):
                    copy_entry(src,out,info,info.filename)
        for batch,path in enumerate(parts[1:],2):
            prefix=f'merged/batch{batch}/'
            with zipfile.ZipFile(path) as src:
                for info in src.infolist():
                    if info.filename in ('[Content_Types].xml','_rels/.rels'):continue
                    copy_entry(src,out,info,prefix+info.filename,True,prefix)
                content=ET.fromstring(src.read('[Content_Types].xml'))
                for entry in content:
                    if entry.tag==f'{{{CT}}}Override':
                        name='/'+prefix+entry.get('PartName').lstrip('/')
                        if name not in overrides:
                            ET.SubElement(types,entry.tag,PartName=name,ContentType=entry.get('ContentType'));overrides.add(name)
                    elif entry.get('Extension') not in defaults:
                        types.append(entry);defaults.add(entry.get('Extension'))
                source_pres=ET.fromstring(src.read('ppt/presentation.xml'))
                source_rels={x.get('Id'):x for x in ET.fromstring(src.read('ppt/_rels/presentation.xml.rels'))}
                for list_name,parent,is_slide in [('sldMasterIdLst',masters,False),('sldIdLst',slides,True)]:
                    source_list=source_pres.find(f'{{{P}}}{list_name}')
                    if source_list is None:continue
                    for entry in source_list:
                        rel=source_rels[entry.get(f'{{{R}}}id')];target=rel.get('Target')
                        full=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('ppt',target))
                        rid=relationship(rel.get('Type'),posixpath.relpath(prefix+full,'ppt'))
                        ident=next_slide if is_slide else next_master
                        ET.SubElement(parent,entry.tag,{'id':str(ident),f'{{{R}}}id':rid})
                        if is_slide:next_slide+=1
                        else:next_master+=1
        out.writestr('ppt/presentation.xml',_xml(pres));out.writestr('ppt/_rels/presentation.xml.rels',_xml(rels));out.writestr('[Content_Types].xml',_xml(types))
        if app is not None:
            for child in app:
                if ET.QName(child).localname=='Slides':child.text=str(len(slides))
            out.writestr('docProps/app.xml',_xml(app))
