import cv2
import numpy as np

from app.point_worker import (
    LEGACY_PAGE1_SEM_BOX,
    crop_page1_layout,
    crop_page2_element_maps,
    remap_normalized_polygons_between_crops,
)


def _page():
    return np.full((1754,1240,3),255,np.uint8)


def _panel(img,x,y,w,h,color=(110,110,110)):
    # Continuous non-white Bruker-like image panel.
    img[y:y+h,x:x+w]=color
    cv2.circle(img,(x+w//2,y+h//2),max(8,min(w,h)//10),(35,35,35),-1)


def test_standard_layout_keeps_historic_crop_exactly():
    p1=_page(); _panel(p1,98,206,628,419); _panel(p1,101,818,1102,734,(80,100,120))
    got=crop_page1_layout(p1)
    assert got['sem'].shape[:2]==(418,628)
    assert got['_layout']['method']=='legacy_standard_profile'
    assert got['_layout']['sem_box']['x0']==98
    assert got['_layout']['sem_box']['y0']==207

    p2=_page()
    for x,y,w,h in [(101,190,542,362),(658,190,542,362),(101,566,542,362),(658,566,542,362),(101,943,542,362)]:
        _panel(p2,x,y,w,h)
    maps=crop_page2_element_maps(p2)
    assert maps['_layout']['profile']=='bruker_standard_362'
    assert maps['o_map'].shape[:2]==(362,542)
    assert maps['_layout']['boxes']['o_map']['y0']==566


def test_tall_layout_is_detected_without_condition_specific_rule():
    p1=_page(); _panel(p1,97,179,630,473); _panel(p1,101,821,1102,827,(80,100,120))
    got=crop_page1_layout(p1)
    assert got['sem'].shape[:2]==(473,630)
    assert got['eds_map'].shape[:2]==(827,1102)
    assert got['_layout']['method']=='auto_nonwhite_components'
    assert got['_layout']['sem_box']['y0']==179

    p2=_page()
    for x,y,w,h in [(101,190,541,407),(658,190,542,407),(101,612,542,407),(658,612,542,407),(101,1034,542,407)]:
        _panel(p2,x,y,w,h)
    maps=crop_page2_element_maps(p2)
    assert maps['_layout']['profile']=='bruker_tall_407'
    assert maps['c_map'].shape[:2]==(407,542)
    assert maps['o_map'].shape[:2]==(407,542)
    assert maps['_layout']['boxes']['o_map']['y0']==612
    assert maps['_layout']['boxes']['si_map']['y0']==1034


def test_human_roi_is_remapped_from_legacy_crop_into_tall_crop():
    old=LEGACY_PAGE1_SEM_BOX
    new={'x0':97,'y0':179,'x1':727,'y1':652}
    poly=[[{'x':0.25,'y':0.25},{'x':0.75,'y':0.25},{'x':0.75,'y':0.75},{'x':0.25,'y':0.75}]]
    out=remap_normalized_polygons_between_crops(poly,old,new)
    assert len(out)==1 and len(out[0])==4
    # New crop contains more image above/below, so the old ROI moves inward vertically.
    ys=[q['y'] for q in out[0]]
    assert min(ys)>0.25
    assert max(ys)<0.75
    # Converting back through page coordinates preserves the same physical point.
    q=poly[0][0]; r=out[0][0]
    old_px=(old['x0']+q['x']*(old['x1']-old['x0']), old['y0']+q['y']*(old['y1']-old['y0']))
    new_px=(new['x0']+r['x']*(new['x1']-new['x0']), new['y0']+r['y']*(new['y1']-new['y0']))
    assert abs(old_px[0]-new_px[0])<1e-3
    assert abs(old_px[1]-new_px[1])<1e-3


def test_tall_layout_normalizes_one_pixel_component_jitter_before_co_overlay():
    # Real Bruker exports can make connected-component rectangles differ by 1-2 px
    # even though C/O/N/Si share one analytical coordinate system.
    p2=_page()
    for x,y,w,h in [
        (101,190,543,408), (658,190,544,409),
        (101,612,542,410), (658,612,543,409),
        (101,1034,542,407),
    ]:
        _panel(p2,x,y,w,h)
    maps=crop_page2_element_maps(p2)
    shapes=[maps[k].shape[:2] for k in ('se_map','c_map','n_map','o_map','si_map')]
    assert len(set(shapes))==1
    from app.point_worker import make_co_overlay
    out=make_co_overlay(maps['c_map'],maps['o_map'])
    assert out.shape[:2]==maps['c_map'].shape[:2]


def test_co_overlay_defensively_accepts_legacy_one_pixel_shape_mismatch():
    from app.point_worker import make_co_overlay
    c=np.zeros((408,543,3),np.uint8)
    o=np.zeros((409,544,3),np.uint8)
    out=make_co_overlay(c,o)
    assert out.shape[:2]==(408,543)
