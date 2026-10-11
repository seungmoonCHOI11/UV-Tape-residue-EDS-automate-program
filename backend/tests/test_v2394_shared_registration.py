import cv2
import numpy as np

from app.point_worker import estimate_sem_to_eds_registration, map_sem_roi_to_eds, map_sem_roi_to_element


def _feature_rich(h=418,w=628,seed=17):
    rng=np.random.default_rng(seed)
    im=np.full((h,w),105,np.uint8)
    # fine texture plus deterministic particle-like structures
    noise=rng.normal(0,16,(h,w)).astype(np.float32)
    im=np.clip(im.astype(np.float32)+noise,0,255).astype(np.uint8)
    for _ in range(55):
        x=int(rng.integers(30,w-30)); y=int(rng.integers(25,int(h*.78)))
        r=int(rng.integers(3,12)); v=int(rng.integers(55,235))
        cv2.circle(im,(x,y),r,v,-1)
    cv2.ellipse(im,(330,190),(95,58),-8,0,360,205,-1)
    cv2.ellipse(im,(330,190),(58,35),-8,0,360,70,3)
    return cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)


def test_similarity_registration_recovers_scale_translation():
    sem=_feature_rich()
    # Page-2 EDS uses a wider FOV: the SEM content therefore appears smaller.
    scale=0.56; ang=np.deg2rad(1.5)
    a=scale*np.cos(ang); b=scale*np.sin(ang)
    M=np.array([[a,-b,92.0],[b,a,58.0]],np.float32)
    se=cv2.warpAffine(sem,M,(542,362),flags=cv2.INTER_LINEAR,borderValue=(100,100,100))
    reg=estimate_sem_to_eds_registration(sem,se)
    assert reg['accepted']
    assert reg['method']=='sift_similarity'
    assert abs(reg['scale']-scale) < 0.08
    assert abs(reg['rotation_deg']-1.5) < 3.0

    roi=np.zeros(sem.shape[:2],np.uint8)
    cv2.ellipse(roi,(330,190),(95,58),-8,0,360,255,-1)
    mapped=map_sem_roi_to_eds(roi,se.shape,reg)
    expected=cv2.warpAffine(roi,M,(542,362),flags=cv2.INTER_NEAREST,borderValue=0)
    inter=cv2.countNonZero(cv2.bitwise_and(mapped,expected))
    union=cv2.countNonZero(cv2.bitwise_or(mapped,expected))
    assert inter/max(union,1) > 0.72


def test_all_element_panels_receive_identical_roi_coordinates():
    sem=_feature_rich(seed=9)
    M=np.array([[0.61,0,80],[0,0.61,45]],np.float32)
    se=cv2.warpAffine(sem,M,(542,362),flags=cv2.INTER_LINEAR,borderValue=(90,90,90))
    reg=estimate_sem_to_eds_registration(sem,se)
    roi=np.zeros(sem.shape[:2],np.uint8); cv2.rectangle(roi,(250,120),(430,260),255,-1)
    # Panel contents/colors may differ; geometry must not.
    c=np.zeros_like(se); c[:,:,2]=130
    o=np.zeros_like(se); o[:,:,0]=150
    cv2.rectangle(o,(0,0),(541,22),(255,0,0),-1)  # unrelated O top band
    mc,ci=map_sem_roi_to_element(roi,c,se,reg,return_info=True)
    mo,oi=map_sem_roi_to_element(roi,o,se,reg,return_info=True)
    assert ci['method']=='shared_page2_geometry'
    assert oi['method']=='shared_page2_geometry'
    assert np.array_equal(mc,mo)
