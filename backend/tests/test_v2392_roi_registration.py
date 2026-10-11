import cv2
import numpy as np

from app.point_worker import estimate_sem_to_eds_registration, map_sem_roi_to_eds


def _scene(h=240,w=360):
    im=np.zeros((h,w,3),np.uint8)
    cv2.ellipse(im,(170,95),(45,28),18,0,360,(180,180,180),-1)
    cv2.circle(im,(230,145),18,(245,245,245),-1)
    cv2.line(im,(70,55),(280,180),(120,120,120),4)
    cv2.rectangle(im,(90,150),(135,185),(210,210,210),-1)
    return im


def test_sem_to_eds_translation_registration_tracks_known_shift():
    sem=_scene()
    h,w=sem.shape[:2]
    dx,dy=13,-9
    M=np.float32([[1,0,dx],[0,1,dy]])
    se=cv2.warpAffine(sem,M,(w,h),borderValue=(0,0,0))
    reg=estimate_sem_to_eds_registration(sem,se)
    assert reg['accepted'] is True
    assert abs(reg['dx_px']-dx) < 2.5
    assert abs(reg['dy_px']-dy) < 2.5


def test_roi_mapping_uses_registration_shift():
    sem=_scene()
    h,w=sem.shape[:2]
    dx,dy=11,7
    M=np.float32([[1,0,dx],[0,1,dy]])
    se=cv2.warpAffine(sem,M,(w,h),borderValue=(0,0,0))
    roi=np.zeros((h,w),np.uint8)
    cv2.circle(roi,(170,95),24,255,-1)
    reg=estimate_sem_to_eds_registration(sem,se)
    mapped=map_sem_roi_to_eds(roi,se.shape,reg)
    ys,xs=np.where(mapped>0)
    assert abs(float(xs.mean())-(170+dx)) < 2.5
    assert abs(float(ys.mean())-(95+dy)) < 2.5


def test_registration_falls_back_safely_without_se_map():
    roi=np.zeros((100,150),np.uint8); cv2.rectangle(roi,(20,30),(50,60),255,-1)
    mapped=map_sem_roi_to_eds(roi,(200,300,3),None)
    assert mapped.shape==(200,300)
    assert cv2.countNonZero(mapped)>0
