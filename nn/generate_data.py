from rf import UCADetector, ULADetector, NoiseWrapper, QAMSource, beamformer
import os
import numpy as np
import pickle
import sys
import argparse
from joblib import Parallel, delayed  
from tqdm import tqdm  

c=3e8 # speed of light

class BoundedPoint:
	def __init__(self,
		pos=np.ones(2)*0.5,
		v=np.zeros(2),
		delta_time=0.05,
		width=128):
		self.pos=pos
		self.v=v
		self.delta_time=delta_time
		self.width=width

	def time_step(self):
		self.pos+=self.v*self.delta_time

		for idx in [0,1]:
			while self.pos[idx]>self.width or self.pos[idx]<0:
				if self.pos[idx]>self.width:
					self.pos[idx]=self.width-self.pos[idx]
				else:
					self.pos[idx]=-self.pos[idx]
				self.v[idx]=-self.v[idx]
		return np.array(self.pos)


def time_to_detector_offset(t,orbital_width,orbital_height,orbital_frequency=1/100.0,phase_offset=0): #1/2.0):
	x=np.cos( 2*np.pi*orbital_frequency*t+phase_offset)*orbital_width
	y=np.sin( 2*np.pi*orbital_frequency*t+phase_offset)*orbital_height
	return np.array([
		1/2+x,
		1/2+y])




def generate_session(args_and_session_idx):
	args,session_idx=args_and_session_idx
	np.random.seed(seed=args.seed+session_idx)
	wavelength=c/args.carrier_frequency

	if args.array_type=='linear':
		d=ULADetector(args.sampling_frequency,args.elements,wavelength/4) # 10Mhz sampling
	elif args.array_type=='circular':
		d=UCADetector(args.sampling_frequency,args.elements,wavelength/4) # 10Mhz sampling
	else:
		print("Array type must be linear or circular")
		sys.exit(1)

	fixed_source_positions=np.random.uniform(low=0, high=args.width,size=(args.sources,2))
	if args.reference:
		fixed_source_positions=fixed_source_positions*0+np.array((width//2,width//4))

	source_IQ=np.random.uniform(0,2*np.pi,size=(args.sources,2))

	detector_position_phase_offset=np.random.uniform(0,2*np.pi)

	sigma=args.sigma
	d.position_offset=0

	# lets run this thing
	time_stamps=[]
	source_positions_at_t=[]
	broadcasting_positions_at_t=[]
	receiver_positions_at_t=[]
	signal_matrixs_at_t=[]
	beam_former_outputs_at_t=[]
	detector_position_phase_offsets_at_t=[]
	detector_position_at_t=[]
	orientation_at_t=[]
	thetas_at_t=[]
	width_at_t=[]

	detector_theta=np.random.uniform(-np.pi,np.pi)
	detector_v=np.array([np.cos(detector_theta),np.sin(detector_theta)])*args.detector_speed # 10m/s
	p1=BoundedPoint(pos=np.random.uniform(0+10,args.width-10,2),
			v=detector_v,
			delta_time=args.time_interval)

	for t_idx in np.arange(args.time_steps):
		t=args.time_interval*t_idx
		time_stamps.append(t)
		width_at_t.append(args.width)

		#only one source transmits at a time, TDM this part
		tdm_source_idx=np.random.randint(0,args.sources)
		d.rm_sources()
		d.add_source(NoiseWrapper(
		  QAMSource(
			fixed_source_positions[tdm_source_idx], # x, y position
			args.carrier_frequency,
			args.signal_frequency,
			sigma=sigma,
			IQ=source_IQ[tdm_source_idx]),
		  sigma=sigma))

		broadcasting=np.zeros((args.sources,1))
		broadcasting[tdm_source_idx]=1
		broadcasting_positions_at_t.append(
			broadcasting
		)

		#set the detector position (its moving)
		if args.detector_trajectory=='orbit':
			d.position_offset=(time_to_detector_offset(t=t,
				orbital_width=1/4,
				orbital_height=1/3,
				phase_offset=detector_position_phase_offset,
				orbital_frequency=(2/3)*args.width*np.pi/args.detector_speed)*args.width).astype(int)
		elif args.detector_trajectory=='bounce':
			d.position_offset=p1.time_step()

		detector_position_phase_offsets_at_t.append(detector_position_phase_offset)
		source_positions_at_t.append(fixed_source_positions)
		receiver_positions_at_t.append(
			np.array([ d.receiver_pos(idx) for idx in np.arange(d.n_receivers()) ])) #
		sm=d.get_signal_matrix(start_time=t,duration=args.samples_per_snapshot/d.sampling_frequency)
		signal_matrixs_at_t.append(sm[None,:])

		thetas,steer_dot_signal,steering_vectors=beamformer(d,sm,args.carrier_frequency,spacing=256+1)
		beam_former_outputs_at_t.append(steer_dot_signal.reshape(1,-1))	
		thetas_at_t.append(thetas.reshape(1,-1))
		orientation_at_t.append(d.orientation)
		detector_position_at_t.append(d.position_offset)
	session={
			'broadcasting_positions_at_t':np.vstack([ x[None] for x in broadcasting_positions_at_t]), # list of (time_steps,sources,1) 
			'source_positions_at_t':np.concatenate([ np.vstack(x)[None] for x in source_positions_at_t],axis=0), # (time_steps,sources,2[x,y])
			'receiver_positions_at_t':np.concatenate([ np.vstack(x)[None] for x in receiver_positions_at_t],axis=0), # (time_steps,receivers,2[x,y])
			'signal_matrixs_at_t':np.vstack(signal_matrixs_at_t), # (time_steps,receivers,samples_per_snapshot)
			'beam_former_outputs_at_t':np.vstack(beam_former_outputs_at_t), #(timesteps,thetas_tested_for_steering)
			'thetas_at_t':np.vstack(thetas_at_t), #(timesteps,thetas_tested_for_steering)
			'detector_position_phase_offsets_at_t':np.array(detector_position_phase_offsets_at_t).reshape(-1,1),
			'time_stamps':np.vstack(time_stamps),
			'width_at_t':np.vstack(width_at_t),
			'orientation_at_t':np.vstack(orientation_at_t),
			'detector_position_at_t':np.vstack([ x[None] for x in detector_position_at_t]), # (time_steps,2[x,y])
		}
	pickle.dump(session,open("/".join([args.output,'session_%08d.pkl' % session_idx]),'wb'))

if __name__=='__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument('--carrier-frequency', type=float, required=False, default=2.4e9)
	parser.add_argument('--signal-frequency', type=float, required=False, default=100e3)
	parser.add_argument('--sampling-frequency', type=float, required=False, default=10e6)
	parser.add_argument('--array-type', type=str, required=False, default='circular',choices=['linear','circular'])
	parser.add_argument('--elements', type=int, required=False, default=11)
	parser.add_argument('--sources', type=int, required=False, default=2)
	parser.add_argument('--seed', type=int, required=False, default=0)
	parser.add_argument('--width', type=int, required=False, default=128)
	parser.add_argument('--detector-trajectory', type=str, required=False, default='orbit',choices=['orbit','bounce'])
	parser.add_argument('--detector-speed', type=float, required=False, default=10.0)
	parser.add_argument('--sigma', type=float, required=False, default=1.0)
	parser.add_argument('--time-steps', type=int, required=False, default=100)
	parser.add_argument('--time-interval', type=float, required=False, default=5.0)
	parser.add_argument('--samples-per-snapshot', type=int, required=False, default=3)
	parser.add_argument('--sessions', type=int, required=False, default=1024)
	parser.add_argument('--output', type=str, required=False, default="sessions-default")
	parser.add_argument('--reference', type=bool, required=False, default=False)
	parser.add_argument('--cpus', type=int, required=False, default=8)
	args = parser.parse_args()
	os.mkdir(args.output)
	pickle.dump(args,open("/".join([args.output,'args.pkl']),'wb'))

	result = Parallel(n_jobs=args.cpus)(delayed(generate_session)((args,session_idx)) for session_idx in tqdm(range(args.sessions)))
