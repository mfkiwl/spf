from rf_torch import SessionsDataset,SessionsDatasetTask2Simple,collate_fn
import torch
import time
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
from tformer import Transformer, TransformerModel
import torchvision
from functools import cache
import argparse

from torch import nn, Tensor
from torch.nn import TransformerEncoder, TransformerEncoderLayer
from torch.utils.data import dataset

torch.set_printoptions(precision=5,sci_mode=False,linewidth=1000)

class TransformerModel(nn.Module):
	def __init__(self,
			d_radio_feature, 
			d_model,
			n_heads,
			d_hid,
			n_layers,
			dropout, 
			n_outputs):
		super().__init__()
		self.model_type = 'Transformer'

		encoder_layers = TransformerEncoderLayer(d_model, n_heads, d_hid, dropout)
		self.transformer_encoder = TransformerEncoder(encoder_layers, n_layers)
		
		self.linear_out = nn.Linear(d_model, n_outputs)
		assert( d_model>d_radio_feature)
		
		self.linear_in = nn.Linear(d_radio_feature, d_model-d_radio_feature)
		
		self.d_model=d_model

	def forward(self, src: Tensor) -> Tensor:
		output = self.transformer_encoder(
			torch.cat(
				[src,self.linear_in(src)],axis=2)) #/np.sqrt(self.d_radio_feature))
		output = self.linear_out(output)/np.sqrt(self.d_model)
		return output

class SnapshotNet(nn.Module):
	def __init__(self,
			snapshots_per_sample=1,
			d_radio_feature=257+4+4,
			d_model=512,
			n_heads=8,
			d_hid=256,
			n_layers=4,
			n_outputs=8,
			dropout=0.0,
			ssn_d_hid=128,
			ssn_n_layers=4,
			ssn_n_outputs=8,
			ssn_d_embed=64,
			ssn_dropout=0.0):
		super().__init__()
		self.d_radio_feature=d_radio_feature
		self.d_model=d_model
		self.n_heads=n_heads
		self.d_hid=d_hid
		self.n_outputs=n_outputs
		self.dropout=dropout

		self.snap_shot_net=SingleSnapshotNet(
			d_radio_feature=d_radio_feature,
			d_hid=ssn_d_hid,
			d_embed=ssn_d_embed,
			n_layers=ssn_n_layers,
			n_outputs=ssn_n_outputs,
			dropout=ssn_dropout)
		#self.snap_shot_net=Task1Net(d_radio_feature*snapshots_per_sample)

		self.tformer=TransformerModel(
			d_radio_feature=d_radio_feature+ssn_d_embed+n_outputs,
			d_model=d_model,
			n_heads=n_heads,
			d_hid=d_hid,
			n_layers=n_layers,
			dropout=dropout,
			n_outputs=n_outputs)

	def forward(self,x):
		single_snapshot_output,embed=self.snap_shot_net(x)
		d=self.snap_shot_net(x)
		#return single_snapshot_output,single_snapshot_output
		tformer_output=self.tformer(
			torch.cat([
				x,
				d['embedding'],
				d['single_snapshot_pred']
				],axis=2))
		return {'transformer_pred':tformer_output,'single_snapshot_pred':d['single_snapshot_pred']}
		

class SingleSnapshotNet(nn.Module):
	def __init__(self,
			d_radio_feature,
			d_hid,
			d_embed,
			n_layers,
			n_outputs,
			dropout,
			snapshots_per_sample=0):
		super(SingleSnapshotNet,self).__init__()
		self.snapshots_per_sample=snapshots_per_sample
		self.d_radio_feature=d_radio_feature
		if self.snapshots_per_sample>0:
			self.d_radio_feature*=snapshots_per_sample
		self.d_hid=d_hid
		self.d_embed=d_embed
		self.n_layers=n_layers
		self.n_outputs=n_outputs
		self.dropout=dropout
		
		self.embed_net=nn.Sequential(
			nn.Linear(self.d_radio_feature,d_hid),
			*[nn.Sequential(
				nn.LayerNorm(d_hid),
				nn.Linear(d_hid,d_hid),
				nn.ReLU()
				)
			for _ in range(n_layers) ],
			nn.LayerNorm(d_hid),
			nn.Linear(d_hid,d_embed),
			nn.LayerNorm(d_embed))
		self.lin_output=nn.Linear(d_embed,self.n_outputs)

	def forward(self, x):
		if self.snapshots_per_sample>0:
			x=x.reshape(x.shape[0],-1)
		embed=self.embed_net(x)
		output=self.lin_output(embed)
		if self.snapshots_per_sample>0:
			output=output.reshape(-1,1,self.n_outputs)
			return {'fc_pred':output}
		return {'single_snapshot_pred':output,'embedding':embed}

class Task1Net(nn.Module):
	def __init__(self,ndim,n_outputs=8):
		super().__init__()
		self.bn1 = nn.BatchNorm1d(120)
		self.bn2 = nn.BatchNorm1d(84)
		self.bn3 = nn.BatchNorm1d(n_outputs)
		self.fc1 = nn.Linear(ndim, 120)
		self.fc2 = nn.Linear(120, 84)
		self.fc3 = nn.Linear(84, n_outputs)
		self.n_outputs=n_outputs
		self.ndim=ndim

	def forward(self, x):
		x = x.reshape(x.shape[0],-1)
		x = F.relu(self.bn1(self.fc1(x)))
		x = F.relu(self.bn2(self.fc2(x)))
		x = F.relu(self.bn3(self.fc3(x)))
		x = x.reshape(-1,1,self.n_outputs)
		return {'fc_pred':x} #.reshape(x.shape[0],1,2)

if __name__=='__main__': 
	parser = argparse.ArgumentParser()
	parser.add_argument('--device', type=str, required=False, default='cpu')
	parser.add_argument('--embedding-warmup', type=int, required=False, default=256*32)
	parser.add_argument('--snapshots-per-sample', type=int, required=False, default=[1,4,8], nargs="+")
	parser.add_argument('--print-every', type=int, required=False, default=100)
	parser.add_argument('--mb', type=int, required=False, default=64)
	parser.add_argument('--workers', type=int, required=False, default=4)
	parser.add_argument('--dataset', type=str, required=False, default='./sessions_task1')
	parser.add_argument('--lr', type=float, required=False, default=0.000001)
	args = parser.parse_args()

	start_time=time.time()

	device=torch.device(args.device)
	print("init dataset")
	ds=SessionsDatasetTask2Simple(args.dataset,snapshots_in_sample=max(args.snapshots_per_sample))
	
	print("init dataloader")
	trainloader = torch.utils.data.DataLoader(
			ds, 
			batch_size=args.mb,
			shuffle=True, 
			num_workers=args.workers,
			collate_fn=collate_fn)

	print("init network")
	nets=[
		                {'name':'%d snapshots' % snapshots_per_sample, 
                'net':SnapshotNet(snapshots_per_sample).to(device),
                 'snapshots_per_sample':snapshots_per_sample}
		for snapshots_per_sample in args.snapshots_per_sample
	]
	for snapshots_per_sample in args.snapshots_per_sample:
		nets.append(
			{'name':'task1net%d' % snapshots_per_sample,
			'net':Task1Net(265*snapshots_per_sample), 'snapshots_per_sample':snapshots_per_sample}
		)
	for snapshots_per_sample in args.snapshots_per_sample:
		nets.append(
			{'name':'SSN%d' % snapshots_per_sample,
			'net':SingleSnapshotNet(d_radio_feature=265,
                        	d_hid=64,
                        	d_embed=64,
                        	n_layers=4,
                        	n_outputs=8,
                        	dropout=0.0,
                        snapshots_per_sample=snapshots_per_sample),
			'snapshots_per_sample':snapshots_per_sample
			}
		)


	fig=plt.figure(figsize=(12,4))

	for d_net in nets:
		d_net['optimizer']=optim.Adam(d_net['net'].parameters(),lr=args.lr)
	criterion = nn.MSELoss()

	print("training loop")
	losses_to_plot={}
	for d_net in nets:
		losses_to_plot[d_net['name']+"_ss"]=[]
		losses_to_plot[d_net['name']+"_tformer"]=[]
	losses_to_plot['baseline']=[]
	running_losses={ d['name']:[] for d in nets}
	running_losses['baseline']=[]

	for epoch in range(200):  # loop over the dataset multiple times
		for i, data in enumerate(trainloader, 0):
			radio_inputs, labels = data
			labels[:,:,2:]=0

			radio_inputs=radio_inputs.to(device)
			labels=labels.to(device)
			for d_net in nets:
				d_net['optimizer'].zero_grad()

				_radio_inputs=torch.cat([
					radio_inputs[:,:d_net['snapshots_per_sample']],
					],dim=2)
				_labels=labels[:,:d_net['snapshots_per_sample']]

				preds=d_net['net'](_radio_inputs)
				losses={}
				transformer_loss=0.0
				single_snapshot_loss=0.0
				fc_loss=0.0
				if 'transformer_pred' in preds:
					transformer_loss = criterion(preds['transformer_pred'],_labels)
					losses['transformer_loss']=transformer_loss.item()
				if 'single_snapshot_pred' in preds:
					single_snapshot_loss = criterion(preds['single_snapshot_pred'],_labels)
					losses['single_snapshot_loss']=single_snapshot_loss.item()
				if 'fc_pred' in preds:
					fc_loss = criterion(preds['fc_pred'],_labels)
					losses['fc_loss']=fc_loss.item()
				loss=transformer_loss+single_snapshot_loss+fc_loss
				if i<args.embedding_warmup:
					loss=single_snapshot_loss+fc_loss
				#if i%1000==0:
				#	print("TFORMER",tformer_preds[0])
				#	print("SINGLE",single_snapshot_preds[0])
				#	print("LABEL",_labels[0])
				loss.backward()
				running_losses[d_net['name']].append(losses) # += np.log(np.array([single_snapshot_loss.item(),tformer_loss.item()]))
				d_net['optimizer'].step()
			running_losses['baseline'].append( {'baseline':criterion(labels*0+labels.mean(axis=[0,1],keepdim=True), labels).item() } )


			def net_to_losses(name):
				rl=running_losses[name]
				if len(rl)==0:
					return {}
				losses={}
				for k in ['baseline','transformer_loss','single_snapshot_loss','fc_loss']:
					if k in rl[0]:
						losses[k]=np.log(np.array( [ np.mean([ l[k] for l in rl[idx*args.print_every:(idx+1)*args.print_every]])  
							for idx in range(len(rl)//args.print_every) ]))
				return losses
				

			def net_to_loss_str(name):
				rl=running_losses[name]
				if len(rl)==0:
					return ""
				loss_str=[name]
				losses=net_to_losses(name)
				for k in ['transformer_loss','single_snapshot_loss','fc_loss']:
					if k in losses:
						loss_str.append("%s:%0.4f" % (k,losses[k][-1]))
				return ",".join(loss_str)

			if i % args.print_every == args.print_every-1:
				loss_str="\t"+"\n\t".join([ net_to_loss_str(d['name']) for d in nets ])
				baseline_loss=net_to_losses('baseline')
				print(f'[{epoch + 1}, {i + 1:5d}]\n\tbaseline: {baseline_loss["baseline"][-1]:.3f} , time { (time.time()-start_time)/i :.3f} / batch' )
				print(loss_str)
				if i//args.print_every>2:
					fig.clf()
					axs=fig.subplots(1,3,sharex=True,sharey=True)
					xs=np.arange(len(baseline_loss['baseline']))*args.print_every
					for i in range(3):
						axs[i].plot(xs,baseline_loss['baseline'],label='baseline')
						axs[i].set_xlabel("time")
						axs[i].set_ylabel("log loss")
					axs[0].set_title("Transformer loss")
					axs[1].set_title("Single snapshot loss")
					axs[2].set_title("FC loss")
					for d_net in nets:
						losses=net_to_losses(d_net['name'])
						if 'transformer_loss' in losses:
							axs[0].plot(xs,losses['transformer_loss'],label=d_net['name'])
						if 'single_snapshot_loss' in losses:
							axs[1].plot(xs,losses['single_snapshot_loss'],label=d_net['name'])
						if 'fc_loss' in losses:
							axs[2].plot(xs,losses['fc_loss'],label=d_net['name'])
					for i in range(3):
						axs[i].legend()
					fig.tight_layout()
					#fig.pause(0.001)
					#plt.ion()     # turns on interactive mode
					plt.pause(0.1)
				#running_losses={ d['name']:np.zeros(2) for d in nets }
		
	print('Finished Training')
