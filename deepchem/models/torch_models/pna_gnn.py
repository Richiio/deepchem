from functools import partial
from math import sqrt

import torch

from deepchem.feat.molecule_featurizers.conformer_featurizer import (
    full_atom_feature_dims,
    full_bond_feature_dims,
)
from deepchem.utils.graph_utils import (
    aggregate_max,
    aggregate_mean,
    aggregate_min,
    aggregate_moment,
    aggregate_std,
    aggregate_sum,
    aggregate_var,
    scale_amplification,
    scale_attenuation,
    scale_identity,
)

PNA_AGGREGATORS = {
    "mean": aggregate_mean,
    "sum": aggregate_sum,
    "max": aggregate_max,
    "min": aggregate_min,
    "std": aggregate_std,
    "var": aggregate_var,
    "moment3": partial(aggregate_moment, n=3),
    "moment4": partial(aggregate_moment, n=4),
    "moment5": partial(aggregate_moment, n=5),
}

PNA_SCALERS = {
    "identity": scale_identity,
    "amplification": scale_amplification,
    "attenuation": scale_attenuation,
}


class AtomEncoder(torch.nn.Module):
    """
    Encodes atom features into embeddings based on the Open Graph Benchmark feature set in conformer_featurizer.

    Parameters
    ----------
    emb_dim : int
        The dimension that the returned embedding will have.
    padding : bool, optional (default=False)
        If true then the last index will be used for padding.

    Examples
    --------
    >>> from deepchem.feat.molecule_featurizers.conformer_featurizer import full_atom_feature_dims
    >>> atom_encoder = AtomEncoder(emb_dim=32)
    >>> num_rows = 10
    >>> atom_features = torch.stack([
    ... torch.randint(low=0, high=dim, size=(num_rows,))
    ... for dim in full_atom_feature_dims
    ... ], dim=1)
    >>> atom_embeddings = atom_encoder(atom_features)
    """

    def __init__(self, emb_dim, padding=False):
        super(AtomEncoder, self).__init__()

        self.atom_embedding_list = torch.nn.ModuleList()
        self.padding = padding

        for dim in full_atom_feature_dims:
            if padding:
                emb = torch.nn.Embedding(dim + 1, emb_dim, padding_idx=0)
            else:
                emb = torch.nn.Embedding(dim, emb_dim)
            torch.nn.init.xavier_uniform_(emb.weight.data)
            self.atom_embedding_list.append(emb)

    def reset_parameters(self):
        """
        Reset the parameters of the atom embeddings.

        This method resets the weights of the atom embeddings by initializing
        them with a uniform distribution between -sqrt(3) and sqrt(3).
        """
        for embedder in self.atom_embedding_list:
            embedder.weight.data.uniform_(-sqrt(3), sqrt(3))

    def forward(self, x):
        """
        Compute the atom embeddings for the given atom features.

        Parameters
        ----------
        x : torch.Tensor, shape (batch_size, num_atoms, num_features)
            The input atom features tensor.

        Returns
        -------
        x_embedding : torch.Tensor, shape (batch_size, num_atoms, emb_dim)
            The computed atom embeddings.
        """
        x_embedding = 0
        for i in range(x.shape[1]):
            if self.padding:
                x_embedding += self.atom_embedding_list[i](x[:, i].long() + 1)
            else:
                x_embedding += self.atom_embedding_list[i](x[:, i].long())

        return x_embedding


class BondEncoder(torch.nn.Module):
    """
    Encodes bond features into embeddings based on the Open Graph Benchmark feature set in conformer_featurizer.

    Parameters
    ----------
    emb_dim : int
        The dimension that the returned embedding will have.
    padding : bool, optional (default=False)
        If true then the last index will be used for padding.

    Examples
    --------
    >>> from deepchem.feat.molecule_featurizers.conformer_featurizer import full_bond_feature_dims
    >>> bond_encoder = BondEncoder(emb_dim=32)
    >>> num_rows = 10
    >>> bond_features = torch.stack([
    ... torch.randint(low=0, high=dim, size=(num_rows,))
    ... for dim in full_bond_feature_dims
    ... ], dim=1)
    >>> bond_embeddings = bond_encoder(bond_features)
    """

    def __init__(self, emb_dim, padding=False):
        super(BondEncoder, self).__init__()

        self.bond_embedding_list = torch.nn.ModuleList()
        self.padding = padding

        for dim in full_bond_feature_dims:
            if padding:
                emb = torch.nn.Embedding(dim + 1, emb_dim, padding_idx=0)
            else:
                emb = torch.nn.Embedding(dim, emb_dim)
            torch.nn.init.xavier_uniform_(emb.weight.data)
            self.bond_embedding_list.append(emb)

    def forward(self, edge_attr):
        """
        Compute the bond embeddings for the given bond features.

        Parameters
        ----------
        edge_attr : torch.Tensor, shape (batch_size, num_edges, num_features)
            The input bond features tensor.

        Returns
        -------
        bond_embedding : torch.Tensor, shape (batch_size, num_edges, emb_dim)
            The computed bond embeddings.
        """
        bond_embedding = 0
        for i in range(edge_attr.shape[1]):
            if self.padding:
                bond_embedding += self.bond_embedding_list[i](
                    edge_attr[:, i].long() + 1)
            else:
                bond_embedding += self.bond_embedding_list[i](
                    edge_attr[:, i].long())

        return bond_embedding


class PNALayer(nn.Module):
    """
    Principal Neighbourhood Aggregation Layer.

    Parameters
    ----------
    in_dim : int
        Input dimension of the node features.
    out_dim : int
        Output dimension of the node features.
    in_dim_edges : int
        Input dimension of the edge features.
    aggregators : List[str]
        List of aggregator functions to use.
    scalers : List[str]
        List of scaler functions to use.
    activation : Union[Callable, str], optional, default="relu"
        Activation function to use.
    last_activation : Union[Callable, str], optional, default="none"
        Last activation function to use.
    dropout : float, optional, default=0.0
        Dropout rate.
    residual : bool, optional, default=True
        Whether to use residual connections.
    pairwise_distances : bool, optional, default=False
        Whether to use pairwise distances.
    mid_batch_norm : bool, optional, default=False
        Whether to use batch normalization in the middle layers.
    last_batch_norm : bool, optional, default=False
        Whether to use batch normalization in the last layer.
    batch_norm_momentum : float, optional, default=0.1
        Momentum for the batch normalization layers.
    avg_d : Dict[str, float], optional, default={"log": 1.0}
        Dictionary containing the average degree of the graph.
    posttrans_layers : int, optional, default=2
        Number of post-transformation layers.
    pretrans_layers : int, optional, default=1
        Number of pre-transformation layers.

    >>> import dgl
    >>> import numpy as np
    >>> import torch
    >>> from deepchem.models.torch_models.pna_gnn import PNALayer
    >>> in_dim = 32
    >>> out_dim = 64
    >>> in_dim_edges = 16
    >>> aggregators = ["mean", "max"]
    >>> scalers = ["identity", "amplification", "attenuation"]
    >>> pna_layer = PNALayer(in_dim=in_dim,
    ...                      out_dim=out_dim,
    ...                      in_dim_edges=in_dim_edges,
    ...                      aggregators=aggregators,
    ...                      scalers=scalers)
    >>> num_nodes = 10
    >>> num_edges = 20
    >>> node_features = torch.randn(num_nodes, in_dim)
    >>> edge_features = torch.randn(num_edges, in_dim_edges)
    >>> g = dgl.graph((np.random.randint(0, num_nodes, num_edges),
    ...                np.random.randint(0, num_nodes, num_edges)))
    >>> g.ndata['feat'] = node_features
    >>> g.edata['feat'] = edge_features
    >>> g.ndata['feat'] = pna_layer(g)
    """

    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        in_dim_edges: int,
        aggregators: List[str],
        scalers: List[str],
        activation: Union[Callable, str] = "relu",
        dropout: float = 0.0,
        residual: bool = True,
        pairwise_distances: bool = False,
        batch_norm_momentum=0.1,
        avg_d: Dict[str, float] = {"log": 1.0},
        posttrans_layers: int = 2,
        pretrans_layers: int = 1,
    ):
        super(PNALayer, self).__init__()
        self.aggregators = [PNA_AGGREGATORS[aggr] for aggr in aggregators]
        self.scalers = [PNA_SCALERS[scale] for scale in scalers]
        self.edge_features = in_dim_edges > 0
        self.activation = activation
        self.avg_d = avg_d
        self.pairwise_distances = pairwise_distances
        self.residual = residual
        if in_dim != out_dim:
            self.residual = False

        self.pretrans = MultilayerPerceptron(
            d_input=(2 * in_dim + in_dim_edges +
                     1) if self.pairwise_distances else
            (2 * in_dim + in_dim_edges),
            d_output=in_dim,
            d_hidden=(in_dim,) * (pretrans_layers - 1),
            batch_norm=True,
            batch_norm_momentum=batch_norm_momentum,
            dropout=dropout)

        self.posttrans = MultilayerPerceptron(
            d_input=(len(self.aggregators) * len(self.scalers) + 1) * in_dim,
            d_hidden=(out_dim,) * (posttrans_layers - 1),
            d_output=out_dim,
            batch_norm=True,
            batch_norm_momentum=batch_norm_momentum,
            dropout=dropout)

    def forward(self, g):
        """
        Forward pass of the PNA layer.

        Parameters
        ----------
        g : dgl.DGLGraph
            Input graph

        Returns
        -------
        h : torch.Tensor
            Node feature tensor
        """
        h = g.ndata['feat']
        h_in = h
        # pretransformation
        g.apply_edges(self.pretrans_edges)

        # aggregation
        g.update_all(self.message_func, self.reduce_func)
        h = torch.cat([h, g.ndata['feat']], dim=-1)
        # post-transformation
        h = self.posttrans(h)
        if self.residual:
            h = h + h_in

        return h

    def message_func(self, edges) -> Dict[str, torch.Tensor]:
        """
        The message function to generate messages along the edges.

        Parameters
        ----------
        edges : dgl.EdgeBatch
            Batch of edges.

        Returns
        -------
        Dict[str, torch.Tensor]
            Dictionary containing the edge features.
        """
        return {"e": edges.data["e"]}

    def reduce_func(self, nodes) -> Dict[str, torch.Tensor]:
        """
        The reduce function to aggregate the messages.
        Apply the aggregators and scalers, and concatenate the results.

        Parameters
        ----------
        nodes : dgl.NodeBatch
            Batch of nodes.

        Returns
        -------
        Dict[str, torch.Tensor]
            Dictionary containing the aggregated node features.
        """
        h_in = nodes.data['feat']
        h = nodes.mailbox["e"]
        D = h.shape[-2]
        h_to_cat = [
            aggr(h=h, h_in=h_in)  # type: ignore
            for aggr in self.aggregators
        ]
        h = torch.cat(h_to_cat, dim=-1)

        if len(self.scalers) > 1:
            h = torch.cat(
                [
                    scale(h, D=D, avg_d=self.avg_d)  # type: ignore
                    for scale in self.scalers
                ],
                dim=-1)

        return {'feat': h}

    def pretrans_edges(self, edges) -> Dict[str, torch.Tensor]:
        """
        Return a mapping to the concatenation of the features from
        the source node, the destination node, and the edge between them (if applicable).

        Parameters
        ----------
        edges : dgl.EdgeBatch
            Batch of edges.

        Returns
        -------
        Dict[str, torch.Tensor]
            Dictionary containing the concatenated features.
        """

        if self.edge_features and self.pairwise_distances:
            squared_distance = torch.sum((edges.src['x'] - edges.dst['x'])**2,
                                         dim=-1)[:, None]
            z2 = torch.cat([
                edges.src['feat'], edges.dst['feat'], edges.data['feat'],
                squared_distance
            ],
                           dim=-1)
        elif not self.edge_features and self.pairwise_distances:
            squared_distance = torch.sum((edges.src['x'] - edges.dst['x'])**2,
                                         dim=-1)[:, None]
            z2 = torch.cat(
                [edges.src['feat'], edges.dst['feat'], squared_distance],
                dim=-1)
        elif self.edge_features and not self.pairwise_distances:
            z2 = torch.cat(
                [edges.src['feat'], edges.dst['feat'], edges.data['feat']],
                dim=-1)
        else:
            z2 = torch.cat([edges.src['feat'], edges.dst['feat']], dim=-1)
        return {"e": self.pretrans(z2)}