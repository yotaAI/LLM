import numpy as np
import math

import torch
import torch.nn as nn
import torch.functional as F
from torch.utils.data import Dataset, DataLoader


# Input Embedding
class InputEmbedding(nn.Module):
	def __init__(self,d_model:int,vocab_size:int):
		super().__init__()
		self.d_model=d_model
		self.embedding= nn.Embedding(vocab_size,d_model)
	def forward(self,x):
		return self.embedding(x) * math.sqrt(self.d_model)

# Positional Bias
class PositionalEmbedding(nn.Module): #(seq_len X d_model)
	def __init__(self,d_model:int,seq_len:int,dropout:None):
		super().__init__()
		self.d_model=d_model
		self.seq_len=seq_len
		self.dropout = nn.Dropout(dropout)

		pe = torch.zeros(seq_len,d_model)
		position = torch.arange(0,seq_len,dtype=torch.float).unsqueeze(-1)
		div_term = torch.exp(torch.arange(0,d_model,2).float() * (-math.log(10000.0)/d_model))

		pe[:,0::2] = torch.sin(position / div_term)
		pe[:,1::2] = torch.cos(position / div_term)

		pe = pe.unsqueeze(0)

		self.register_buffer('pe',pe)
	def forward(self,x):
		x = x + (self.pe[:,:x.shape[1],:]).requires_grad_(False)
		return self.dropout(x)


# Attention Layer
class MultiHeadAttentionLayer(nn.Module):
	def __init__(self,d_model:int, h:int, dropout:float):
		super().__init__()
		self.d_model = d_model
		self.h = h
		assert d_model % h ==0, "d_model is not divisible by h"

		self.d_k = d_model // h
		self.w_q = nn.Linear(d_model,d_model) #Wq
		self.w_k = nn.Linear(d_model,d_model) #Wk
		self.w_v = nn.Linear(d_model,d_model) #Wv
		self.w_o = nn.Linear(d_model,d_model) #Wo

		self.dropout = nn.Dropout(dropout)

	@staticmethod
	def attention(query,key,value,mask,dropout:nn.Dropout):
		d_k = query.shape[-1]
		attention_score = (query @ key.transpose(-2,-1)) / math.sqrt(d_k)

		if mask is not None:
			attention_score.masked_fill_(mask == 0,-1e9)
		attention_score = attention_score.softmax(dim=-1) #(batch_dim, h, seq_len, seq_len)
		if dropout is not None:
			attention_score = dropout(attention_score)

		return (attention_score @ value), attention_score

	def forward(self, q, k, v, mask):
		query = self.w_q(q)
		key = self.w_k(k)
		value = self.w_v(v)

		query = query.view(query.shape[0], query.shape[1], self.h, self.d_k).transpose(1,2)
		key = key.view(key.shape[0], key.shape[1], self.h, self.d_k).transpose(1,2)
		value = value.view(value.shape[0], value.shape[1], self.h, self.d_k).transpose(1,2)

		x, self.attention_score = MultiHeadAttentionLayer.attention(query,key,value,mask,self.dropout)

		x = x.transpose(1,2).contiguous().view(x.shape[0], -1, self.h * self.d_k)

		return self.w_o(x)


# Feed Forward
class FeedForwardBlock(nn.Module):
	def __init__(self, d_model: int, d_ff: int, dropout: float):
		super().__init__()
		self.linear_1 = nn.Linear(d_model,d_ff)
		self.dropout = nn.Dropout(dropout)
		self.linear_2 = nn.Linear(d_ff, d_model)

	def forward(self, x):
		return self.linear_2(self.dropout(torch.relu(self.linear_1(x))))


# Layer Normalization
class LayerNormalization(nn.Module):
	def __init__(self, eps:float = 1e-6):
		super().__init__()
		self.eps= eps
		self.alpha = nn.Parameter(torch.ones(1))
		self.bias = nn.Parameter(torch.zeros(1))

	def forward(self,x):
		mean = x.mean(dim = -1,  keepdims = True)
		std = x.std(dim = -1, keepdim = True)

		return self.alpha * (x - mean) / (std + self.eps) + self.bias

# Residual Connection

class ResidualConnection(nn.Module):
	def __init__(self, dropout: float):
		super().__init__()
		self.dropout = nn.Dropout(dropout)
		self.norm = LayerNormalization()

	def forward(self,x, sublayer):
		return x + self.dropout(sublayer(self.norm(x)))


# Encoder Layer
class EncoderBlock(nn.Module):
	def __init__(self,self_attention_block:MultiHeadAttentionLayer,feed_forward_block:FeedForwardBlock,dropout:nn.Dropout):
		super().__init__()
		self.self_attention_block = self_attention_block
		self.feed_forward_block = feed_forward_block
		self.residual_connections = nn.ModuleList([ResidualConnection(dropout) for _ in range(2)])

	def forward(self,x,src_mask):
		x = self.residual_connections[0](x, lambda x: self.self_attention_block(x,x,x,src_mask))
		x = self.residual_connections[0](x, lambda x: self.feed_forward_block(x))
		return x

class Encoder(nn.Module):
	def __init__(self, layers: nn.ModuleList):
		super().__init__()
		self.layers = layers
		self.norm = LayerNormalization()

	def forward(self, x, mask):
		for layer in self.layers:
			x = layer(x,mask)
		return self.norm(x)



# Decoder Block
class DecoderBlock(nn.Module):
	def __init__(self,self_attention_block:MultiHeadAttentionLayer, cross_attention_block:MultiHeadAttentionLayer,feed_forward_block:FeedForwardBlock,dropout:nn.Dropout):
		super().__init__()
		self.self_attention_block = self_attention_block
		self.cross_attention_block = cross_attention_block
		self.feed_forward_block = feed_forward_block
		self.residual_connections = nn.ModuleList([ResidualConnection(dropout) for _ in range(3)])
	def forward(self, x, encoder_output, src_mask,tgt_mask):
		x = self.residual_connections[0](x, lambda x: self.self_attention_block(x,x,x,tgt_mask))
		x = self.residual_connections[1](x, lambda x: self.cross_attention_block(x, encoder_output,encoder_output,src_mask))
		x = self.residual_connections[2](x,self.feed_forward_block)
		return x

class Decoder(nn.Module):
 	def __init__(self,layers:nn.ModuleList):
 		super().__init__()
 		self.layers = layers
 		self.norm = LayerNormalization()
 	def forward(self, x, encoder_output, src_mask,tgt_mask):

 		for layer in self.layers:
 			x = layer(x,encoder_output,src_mask,tgt_mask)
 		return self.norm(x)

#Final Projection Layer
class ProjectionLayer(nn.Module):
	def __init__(self,d_model:int, vocab_size:int):
		super().__init__()
		self.d_model=d_model
		self.vocab_size=vocab_size

		self.proj = nn.Linear(d_model,vocab_size)
		# print(f'Vocab Size : {vocab_size}')
	def forward(self,x):
		return torch.log_softmax(self.proj(x),dim=-1)

# Transformer Block
class Transformer(nn.Module):
	def __init__(self,encoder:Encoder,decoder:Decoder,src_embed:InputEmbedding,tgt_embed:InputEmbedding,src_pos:PositionalEmbedding,tgt_pos:PositionalEmbedding,projection_layer:ProjectionLayer):
		super().__init__()
		self.encoder=encoder
		self.decoder=decoder
		self.src_embed=src_embed
		self.tgt_embed = tgt_embed
		self.src_pos=src_pos
		self.tgt_pos=tgt_pos
		self.projection_layer=projection_layer

	def encode(self,src,src_mask):
		src = self.src_embed(src)
		src = self.src_pos(src)
		return self.encoder(src,src_mask)
	def decode(self,encoder_output,src_mask,tgt,tgt_mask):
		tgt = self.tgt_embed(tgt)
		tgt = self.tgt_pos(tgt)
		return self.decoder(tgt,encoder_output,src_mask,tgt_mask)
	def projection(self,x):
		return self.projection_layer(x)

#Building Transformer
def build_transformer(src_vocab_size:int, tgt_vocab_size:int,src_seq_len:int, tgt_seq_len:int , d_model:int=512,N:int=6,h:int=8,dropout:float=0.1,d_ff:int=2048):
	#Embedding layer
	src_embed = InputEmbedding(d_model,src_vocab_size)
	tgt_embed = InputEmbedding(d_model,tgt_vocab_size)

	#Creating Positional Encoding
	src_pos = PositionalEmbedding(d_model,src_seq_len,dropout)
	tgt_pos = PositionalEmbedding(d_model,tgt_seq_len,dropout)

	#Creating Encoder blocks
	encoder_blocks = []
	for _ in range(N):
		encoder_self_attention_block = MultiHeadAttentionLayer(d_model,h,dropout)
		feed_forward_block = FeedForwardBlock(d_model,d_ff,dropout)
		encoder_block = EncoderBlock(encoder_self_attention_block,feed_forward_block,dropout)
		encoder_blocks.append(encoder_block)


	#Creating Decoder blocks
	decoder_blocks = []
	for _ in range(N):
		decoder_self_attention_block = MultiHeadAttentionLayer(d_model,h,dropout)
		decoder_cross_attention_block = MultiHeadAttentionLayer(d_model,h,dropout)
		feed_forward_block = FeedForwardBlock(d_model,d_ff,dropout)

		decoder_block = DecoderBlock(decoder_self_attention_block,decoder_cross_attention_block,feed_forward_block,dropout)
		decoder_blocks.append(decoder_block)

	#Creating Encoder and Decoder
	encoder = Encoder(nn.ModuleList(encoder_blocks))	
	decoder = Decoder(nn.ModuleList(decoder_blocks))	

	#Creating Projection Layer
	projection_layer = ProjectionLayer(d_model,tgt_vocab_size)

	#Creating Transformer
	transformer = Transformer(encoder,decoder,src_embed,tgt_embed,src_pos,tgt_pos,projection_layer)


	#Initialization of parameters
	for p in transformer.parameters():
		if p.dim()>1:
			nn.init.xavier_uniform_(p)

	return transformer



if __name__=='__main__':

	transformer = build_transformer(src_vocab_size=96000, tgt_vocab_size=96000,src_seq_len=128, tgt_seq_len=128 , d_model=512,N=6,h=8,dropout=0.1,d_ff=2048)
	print(transformer)


