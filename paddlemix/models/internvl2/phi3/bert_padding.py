# Copyright (c) 2024 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import paddle
import paddle.nn.functional as F
from einops import rearrange, repeat


class IndexFirstAxis(paddle.autograd.PyLayer):

    @staticmethod
    def forward(ctx, input, indices):
        ctx.save_for_backward(indices)
        assert input.ndim >= 2
        ctx.first_axis_dim, other_shape = tuple(input.shape)[0], tuple(input
            .shape)[1:]
        second_dim = other_shape.size
        return paddle.take_along_axis(arr=rearrange(input,
            'b ... -> b (...)'), axis=0, indices=repeat(indices, 'z -> z d',
            d=second_dim)).reshape(-1, *other_shape)

    @staticmethod
    def backward(ctx, grad_output):
        """Class Attribute: torch.autograd.function.FunctionCtx.saved_tensors, can not convert, please check whether it is torch.Tensor.*/torch.autograd.function.FunctionCtx.*/torch.distributions.Distribution.* and convert manually"""
        (indices,) = ctx.saved_tensor()
        assert grad_output.ndim >= 2
        other_shape = tuple(grad_output.shape)[1:]
        grad_output = rearrange(grad_output, 'b ... -> b (...)')
        grad_input = paddle.zeros(shape=[ctx.first_axis_dim, tuple(
            grad_output.shape)[1]], dtype=grad_output.dtype)

        grad_input.put_along_axis_(axis=0, indices=repeat(indices,
            'z -> z d', d=tuple(grad_output.shape)[1]), values=grad_output)
        return grad_input.reshape(ctx.first_axis_dim, *other_shape), None


index_first_axis = IndexFirstAxis.apply


class IndexPutFirstAxis(paddle.autograd.PyLayer):

    @staticmethod
    def forward(ctx, values, indices, first_axis_dim):
        ctx.save_for_backward(indices)
        assert indices.ndim == 1
        assert values.ndim >= 2
        output = paddle.zeros(shape=[first_axis_dim, *tuple(values.shape)[1
            :]], dtype=values.dtype)
        output[indices] = values
        return output

    @staticmethod
    def backward(ctx, grad_output):
        """Class Attribute: torch.autograd.function.FunctionCtx.saved_tensors, can not convert, please check whether it is torch.Tensor.*/torch.autograd.function.FunctionCtx.*/torch.distributions.Distribution.* and convert manually"""
        (indices,) = ctx.saved_tensor()
        grad_values = grad_output[indices]
        return grad_values, None, None


index_put_first_axis = IndexPutFirstAxis.apply


class IndexFirstAxisResidual(paddle.autograd.PyLayer):

    @staticmethod
    def forward(ctx, input, indices):
        ctx.save_for_backward(indices)
        assert input.ndim >= 2
        ctx.first_axis_dim, other_shape = tuple(input.shape)[0], tuple(input
            .shape)[1:]
        second_dim = other_shape.size
        output = input[indices]
        return output, input.detach()

    @staticmethod
    def backward(ctx, grad_output, grad_residual):
        """Class Attribute: torch.autograd.function.FunctionCtx.saved_tensors, can not convert, please check whether it is torch.Tensor.*/torch.autograd.function.FunctionCtx.*/torch.distributions.Distribution.* and convert manually"""
        (indices,) = ctx.saved_tensor()
        assert grad_output.ndim >= 2
        other_shape = tuple(grad_output.shape)[1:]
        assert tuple(grad_residual.shape)[1:] == other_shape
        grad_input = grad_residual
        indices = indices.reshape(tuple(indices.shape)[0], *((1,) * (
            grad_output.ndim - 1)))
        indices = indices.expand_as(y=grad_output)
        grad_input.put_along_axis_(axis=0, indices=indices, values=
            grad_output, reduce='add')
        return grad_input.reshape(ctx.first_axis_dim, *other_shape), None


index_first_axis_residual = IndexFirstAxisResidual.apply


def unpad_input(hidden_states, attention_mask):
    """
    Arguments:
        hidden_states: (batch, seqlen, ...)
        attention_mask: (batch, seqlen), bool / int, 1 means valid and 0 means not valid.
    Return:
        hidden_states: (total_nnz, ...), where total_nnz = number of tokens in selected in attention_mask.
        indices: (total_nnz), the indices of non-masked tokens from the flattened input sequence.
        cu_seqlens: (batch + 1), the cumulative sequence lengths, used to index into hidden_states.
        max_seqlen_in_batch: int
    """
    seqlens_in_batch = attention_mask.sum(axis=-1, dtype='int32')
    indices = paddle.nonzero(attention_mask.flatten(), as_tuple=False).flatten()
    max_seqlen_in_batch = seqlens_in_batch.max().item()
    # cu_seqlens = paddle_aux._FUNCTIONAL_PAD(pad=(1, 0), x=paddle.cumsum(x=
    #     seqlens_in_batch, axis=0, dtype='int32'))
    cu_seqlens = F.pad(paddle.cumsum(seqlens_in_batch, axis=0, dtype="int32"), [1, 0])

    return index_first_axis(rearrange(hidden_states, 'b s ... -> (b s) ...'
        ), indices), indices, cu_seqlens, max_seqlen_in_batch


def pad_input(hidden_states, indices, batch, seqlen):
    """
    Arguments:
        hidden_states: (total_nnz, ...), where total_nnz = number of tokens in selected in attention_mask.
        indices: (total_nnz), the indices that represent the non-masked tokens of the original padded input sequence.
        batch: int, batch size for the padded sequence.
        seqlen: int, maximum sequence length for the padded sequence.
    Return:
        hidden_states: (batch, seqlen, ...)
    """
    output = index_put_first_axis(hidden_states, indices, batch * seqlen)
    return rearrange(output, '(b s) ... -> b s ...', b=batch)

