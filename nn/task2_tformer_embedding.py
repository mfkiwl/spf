from rf_torch import SessionsDataset,SessionsDatasetTask2Simple,collate_fn
import torch
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

class TransformerModel(nn.Module):
	def __init__(self,
			d_radio, 
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
		assert( d_model>d_radio)
		
		self.linear_in = nn.Linear(d_radio, d_model-d_radio)
		
		self.d_model=d_model

	def forward(self, src: Tensor) -> Tensor:
		output = self.transformer_encoder(
			torch.cat(
				[src,self.linear_in(src)],axis=2)) #/np.sqrt(self.d_radio))
		output = self.linear_out(output)/np.sqrt(self.d_model)
		return output

class SnapshotNet(nn.Module):
	def __init__(self,
			snapshots_per_sample=1,
			d_radio=257+4+4,
			d_model=512,
			n_heads=8,
			d_hid=128,
			n_layers=4,
			n_outputs=8,
			dropout=0.0,
			ssn_d_hid=128,
			ssn_n_layers=4,
			ssn_n_outputs=8,
			ssn_d_embed=64,
			ssn_dropout=0.0):
		super().__init__()
		self.d_radio=d_radio
		self.d_model=d_model
		self.n_heads=n_heads
		self.d_hid=d_hid
		self.n_outputs=n_outputs
		self.dropout=dropout

		self.snap_shot_net=SingleSnapshotNet(
			d_radio=d_radio,
			d_hid=ssn_d_hid,
			d_embed=ssn_d_embed,
			n_layers=ssn_n_layers,
			n_outputs=ssn_n_outputs,
			dropout=ssn_dropout)
		#self.snap_shot_net=Task1Net(d_radio*snapshots_per_sample)

		self.tformer=TransformerModel(
			d_radio=d_radio+ssn_d_embed+n_outputs,
			d_model=d_model,
			n_heads=n_heads,
			d_hid=d_hid,
			n_layers=n_layers,
			dropout=dropout,
			n_outputs=n_outputs)

	def forward(self,x):
		single_snapshot_output,embed=self.snap_shot_net(x)
		#return single_snapshot_output,single_snapshot_output
		tformer_output=self.tformer(
			torch.cat([
				x,
				embed,
				single_snapshot_output
				],axis=2))
		return tformer_output,single_snapshot_output
		

class SingleSnapshotNet(nn.Module):
	def __init__(self,
			d_radio,
			d_hid,
			d_embed,
			n_layers,
			n_outputs,
			dropout):
		super(SingleSnapshotNet,self).__init__()
		self.d_hid=d_hid
		self.d_embed=d_embed
		self.n_layers=n_layers
		self.n_outputs=n_outputs
		self.dropout=dropout
		
		self.embed_net=nn.Sequential(
			nn.Linear(d_radio,d_hid),
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
		embed=self.embed_net(x)
		output=self.lin_output(embed)
		return output,embed

class Task1Net(nn.Module):
	def __init__(self,ndim):
		super().__init__()
		self.bn1 = nn.BatchNorm1d(120)
		self.bn2 = nn.BatchNorm1d(84)
		self.bn3 = nn.BatchNorm1d(4)
		self.fc1 = nn.Linear(ndim, 120)
		self.fc2 = nn.Linear(120, 84)
		self.fc3 = nn.Linear(84, 4)

	def forward(self, x):
		x = x.reshape(x.shape[0],-1)
		x = F.relu(self.bn1(self.fc1(x)))
		x = F.relu(self.bn2(self.fc2(x)))
		x = F.relu(self.bn3(self.fc3(x)))
		x = x.reshape(-1,1,4)
		return x,x #.reshape(x.shape[0],1,2)

if __name__=='__main__': 
	parser = argparse.ArgumentParser()
	parser.add_argument('--device', type=str, required=False, default='cpu')
	parser.add_argument('--embedding-warmup', type=int, required=False, default=256*8)
	parser.add_argument('--snapshots-per-sample', type=int, required=False, default=[1,4,8], nargs="+")
	parser.add_argument('--print-every', type=int, required=False, default=200)
	parser.add_argument('--mb', type=int, required=False, default=64)
	parser.add_argument('--workers', type=int, required=False, default=4)
	parser.add_argument('--dataset', type=str, required=False, default='./sessions_task1')
	parser.add_argument('--lr', type=float, required=False, default=0.0000001)
	args = parser.parse_args()

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
	net_factory = lambda snapshots_per_sample:  SnapshotNet(snapshots_per_sample).to(device) 
	nets=[
		                {'name':'%d snapshots' % snapshots_per_sample, 
                'net':net_factory(snapshots_per_sample),
                 'snapshots_per_sample':snapshots_per_sample}
		for snapshots_per_sample in args.snapshots_per_sample
	]

	for d_net in nets:
		d_net['optimizer']=optim.Adam(d_net['net'].parameters(),lr=args.lr)
	criterion = nn.MSELoss()

	print("training loop")
	losses_to_plot={}
	for d_net in nets:
		losses_to_plot[d_net['name']+"_ss"]=[]
		losses_to_plot[d_net['name']+"_tformer"]=[]
	losses_to_plot['baseline']=[]
	running_losses={ d['name']:np.zeros(2) for d in nets }
	baseline_loss = 0.0

	for epoch in range(200):  # loop over the dataset multiple times
		for i, data in enumerate(trainloader, 0):
			radio_inputs, labels = data
			radio_inputs=radio_inputs.to(device)
			labels=labels.to(device)
			for d_net in nets:
				d_net['optimizer'].zero_grad()

				_radio_inputs=torch.cat([
					radio_inputs[:,:d_net['snapshots_per_sample']],
					],dim=2)
				_labels=labels[:,:d_net['snapshots_per_sample']]

				tformer_preds,single_snapshot_preds=d_net['net'](
					_radio_inputs) # 8,32,2
				tformer_loss = criterion(
					tformer_preds,
					_labels)
				single_snapshot_loss = criterion(
					single_snapshot_preds,
					_labels)
				loss=tformer_loss+single_snapshot_loss
				if i<args.embedding_warmup:
					loss=single_snapshot_loss
				if i%1000==0:
					print(tformer_preds[0])
					print(single_snapshot_preds[0])
					print(_labels[0])
				loss.backward()
				running_losses[d_net['name']] += np.log(np.array([single_snapshot_loss.item(),tformer_loss.item()]))
				d_net['optimizer'].step()
			#baseline_loss += ds.args.width*np.sqrt(criterion(torch.zeros(label_images.shape)+label_images.mean(), label_images).item())
			baseline_loss += np.log(criterion(labels*0+labels.mean(axis=[0,1],keepdim=True), labels).item())


			if i % args.print_every == args.print_every-1:
				loss_str=",".join([ "%s: %0.3f / %0.3f" % (d_net['name'],
						running_losses[d_net['name']][0]/args.print_every,
						running_losses[d_net['name']][1]/args.print_every) for d_net in nets ])
				print(f'[{epoch + 1}, {i + 1:5d}] err_in_meters {loss_str} baseline: {baseline_loss / args.print_every:.3f}')
				if i//args.print_every>2:
					for d_net in nets:
						losses_to_plot[d_net['name']+"_ss"].append(running_losses[d_net['name']][0]/args.print_every)
						losses_to_plot[d_net['name']+"_tformer"].append(running_losses[d_net['name']][1]/args.print_every)
					losses_to_plot['baseline'].append(baseline_loss / args.print_every)
					plt.clf()
					for d_net in nets:
						plt.plot(losses_to_plot[d_net['name']+'_ss'],label=d_net['name']+'_ss')
					for d_net in nets:
						plt.plot(losses_to_plot[d_net['name']+'_tformer'],label=d_net['name']+'_tformer')
					plt.plot(losses_to_plot['baseline'],label='baseline')
					plt.xlabel("time")
					plt.ylabel("error in m")
					plt.legend()
					plt.pause(0.001)
				running_losses={ d['name']:np.zeros(2) for d in nets }
				baseline_loss = 0.0
		
	print('Finished Training')