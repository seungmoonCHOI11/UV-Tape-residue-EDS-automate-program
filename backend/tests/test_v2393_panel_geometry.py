import cv2
import numpy as np

from app.point_worker import (
    estimate_panel_content_rect,
    estimate_sem_to_eds_registration,
    map_sem_roi_to_element,
)


def _texture(h, w, seed=7):
    rng=np.random.default_rng(seed)
    base=rng.normal(110,24,(h,w)).clip(0,255).astype(np.uint8)
    cv2.ellipse(base,(w//2,h//2),(max(12,w//7),max(10,h//6)),15,0,360,220,-1)
    return cv2.cvtColor(base,cv2.COLOR_GRAY2BGR)


def _panel(h=240,w=360,rect=(20,30,340,215),seed=7,uniform_top=None):
    out=np.zeros((h,w,3),np.uint8)
    x0,y0,x1,y1=rect
    tex=_texture(y1-y0,x1-x0,seed)
    out[y0:y1,x0:x1]=tex
    if uniform_top is not None:
        yb0,yb1,color=uniform_top
        out[yb0:yb1,x0:x1]=color
    return out


def test_content_rect_ignores_uniform_panel_bands():
    im=_panel(rect=(18,42,342,220),uniform_top=(42,58,(120,0,0)))
    r=estimate_panel_content_rect(im)
    assert r['confidence'] > 0
    # The uniform blue band must not define the image origin.
    assert r['y0'] >= 54
    assert r['x0'] <= 30
    assert r['x1'] >= 330


def test_element_mapping_uses_each_panel_own_content_rect():
    # SEM and SE share the same active geometry after resizing/registration.
    sem=_panel(h=240,w=360,rect=(20,30,340,215),seed=11)
    se=sem.copy()
    # C and O panels contain the same analytical image in differently offset rectangles.
    c=_panel(h=240,w=360,rect=(18,38,342,223),seed=11)
    o=_panel(h=240,w=360,rect=(22,62,338,230),seed=11,uniform_top=(62,78,(120,0,0)))
    roi=np.zeros((240,360),np.uint8)
    cv2.circle(roi,(180,115),28,255,-1)
    reg=estimate_sem_to_eds_registration(sem,se)
    mc,ci=map_sem_roi_to_element(roi,c,se,reg,return_info=True)
    mo,oi=map_sem_roi_to_element(roi,o,se,reg,return_info=True)
    assert ci['method']=='se_to_element_content_rect'
    assert oi['method']=='se_to_element_content_rect'
    cy,cx=np.where(mc>0)
    oy,ox=np.where(mo>0)
    # O content starts materially lower than C, so its mapped ROI should too.
    assert oy.mean() > cy.mean() + 10
    assert 160 < cx.mean() < 200
    assert 160 < ox.mean() < 200


def test_mapping_never_optimizes_toward_element_brightness():
    sem=_panel(h=220,w=320,rect=(15,25,305,205),seed=13)
    se=sem.copy()
    target=_panel(h=220,w=320,rect=(15,45,305,215),seed=13)
    # Add an unrelated very bright spot far from the ROI. Geometry mapping should ignore it.
    cv2.circle(target,(285,185),18,(255,255,255),-1)
    roi=np.zeros((220,320),np.uint8); cv2.circle(roi,(120,95),20,255,-1)
    reg=estimate_sem_to_eds_registration(sem,se)
    mapped=map_sem_roi_to_element(roi,target,se,reg)
    ys,xs=np.where(mapped>0)
    assert xs.mean() < 170
    assert ys.mean() < 150
