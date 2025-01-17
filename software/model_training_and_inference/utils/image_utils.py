from functools import lru_cache

import numpy as np
import torch
import torchvision


@lru_cache
def get_grid(width):
  _xy=np.arange(width).astype(np.int16)
  _x,_y=np.meshgrid(_xy,_xy)
  return np.stack((_x,_y)).transpose(2,1,0)
  #return np.concatenate([_y[None],_x[None]]).transpose(0,2)

#input b,s,2   output: b,s,1,width,width
def detector_positions_to_distance(detector_positions,width):
  diffs=get_grid(width)[None,None]-detector_positions[:,:,None,None].astype(np.float32)
  return (np.sqrt(np.power(diffs, 2).sum(axis=4)))[:,:,None] # batch, snapshot, 1,x ,y 

#input b,s,2   output: b,s,1,width,width
def detector_positions_to_theta_grid(detector_positions,width,img_width):
  bin_size=np.ceil(width/img_width)
  diffs=get_grid(img_width)[None,None]*bin_size-detector_positions[:,:,None,None].astype(np.float32) 
  #return (np.arctan2(diffs[...,1],diffs[...,0]))[:,:,None] # batch, snapshot,1, x ,y 
  return (np.arctan2(diffs[...,0],diffs[...,1]))[:,:,None] # batch, snapshot,1, x ,y  # get angle from x=0, y+ to the right
  
def blur2(img):
  blur=torchvision.transforms.GaussianBlur(11, sigma=8.0)
  return blur(blur(img))

def blur5(img):
  blur=torchvision.transforms.GaussianBlur(11, sigma=8.0)
  return blur(blur(blur(blur(blur(img)))))
def blur10(img):
  return blur5(blur5(img))
      
def labels_to_source_images(labels,width,img_width=128):
  b,s,n_sources,_=labels.shape
  offset=0 #50 # takes too much compute!
  label_images=torch.zeros((
      b,s,
      img_width+2*offset,
      img_width+2*offset))
  bin_size=np.ceil(width/img_width)
  for b_idx in np.arange(b):
    for s_idx in np.arange(s):
      for source_idx in np.arange(n_sources):
        source_x,source_y=labels[b_idx,s_idx,source_idx]
        if s_idx==0:
          print(labels[b_idx,s_idx,source_idx])
          print(int(source_x/bin_size)+offset,int(source_y/bin_size)+offset)
        label_images[b_idx,s_idx,int(source_x/bin_size)+offset,int(source_y/bin_size)+offset]=1
  
  label_images=blur5(
    label_images.reshape(
      b*s,1,
      img_width+2*offset,
      img_width+2*offset)).reshape(
        b,s,1,
        img_width+2*offset,
        img_width+2*offset)    
  #label_images=label_images[...,offset:-offset,offset:-offset] # trim the rest
  assert(label_images.shape[3]==img_width)
  label_images=label_images/label_images.sum(axis=[3,4],keepdims=True)
  return label_images

def radio_to_image(beam_former_outputs_at_t,theta_at_pos,detector_orientation): 
  #theta_at_pos=(theta_at_pos+np.pi-detector_orientation[...,None,None])%(2*np.pi)
  #theta_at_pos=(theta_at_pos+detector_orientation[...,None,None])%(2*np.pi)
  theta_at_pos=(theta_at_pos-detector_orientation[...,None,None])%(2*np.pi)
  #theta_idxs=(((theta_at_pos+np.pi)/(2*np.pi))*(beam_former_outputs_at_t.shape[-1]-1)).round().astype(int)
  theta_idxs=(((theta_at_pos/(2*np.pi)+0.5)%1.0)*(beam_former_outputs_at_t.shape[-1]-1)).round().astype(int)
  b,s,_,width,_=theta_at_pos.shape
  #return theta_at_pos.reshape(b,s,1,width,width)
  beam_former_outputs_at_t=beam_former_outputs_at_t#[:]/beam_former_outputs_at_t.sum(axis=2,keepdims=True)
  outputs=[]
  for b_idx in np.arange(b):
    for s_idx in np.arange(s):
      outputs.append(
        np.take(
          beam_former_outputs_at_t[b_idx,s_idx],
          theta_idxs[b_idx,s_idx].reshape(-1)).reshape(theta_idxs[b_idx,s_idx].shape))
  return np.stack(outputs).reshape(b,s,1,width,width)
