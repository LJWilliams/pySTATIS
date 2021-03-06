'''
Python implementation of STATIS
'''

from __future__ import print_function, absolute_import

import numpy as np
from scipy.stats.mstats import zscore

from .contrib import *
from .decomposition import *
from .helpers import *


class STATISData(object):
    def __init__(self, X, ID, ev=None, groups=['group_1', 'group_2'], normalize=('zscore', 'norm_one'), col_names=None,
                 row_names=None, hdf5=None):
        """
        X: input variables for a single entity
        ID: ID of the entity; can be a set
        ev: eigenvalues of the X columns, in case that X are principal components
        col_names, row_names: labels for rows and columns
        normalize: normalization method to use (None, 'zscore', 'double_center')
        hdf5: reference to hdf5 file
        """

        self.data = X
        self.ev = ev
        self.ID = ID
        self.n_var = X.shape[1]
        self.groups = groups
        self.data_std_ = None
        self.affinity_ = None
        self.hdf5 = hdf5

        if col_names is None:
            self.col_names = ['col_%s' % str(i).zfill(5) for i in range(X.shape[1])]
        else:
            self.col_names = col_names

        if row_names is None:
            self.row_names = ['row_%s' % str(i).zfill(5) for i in range(X.shape[0])]
        else:
            self.row_names = col_names

        self.normalize(method=normalize)

        if ev is not None:
            self.data_scaled_ = self.data_std_ * self.ev
        else:
            self.data_scaled_ = self.data_std_

        if hdf5 is not None:
            if 'affinity' not in hdf5.keys():
                self.hdf5.create_group('affinity')
            else:
                self.hdf5 = hdf5['affinity']

    def normalize(self, method=None):

        if method is 'None':
            print("Not performing any normalizations")
            self.data_std_ = self.data
        else:
            temp = self.data
            for m in method:
                if m is 'zscore':
                    temp = zscore(temp, axis=0, ddof=1)

                elif m is 'norm_one':
                    temp = temp / np.linalg.norm(temp)

                else:
                    print("Method not implemented, use 'zscore' and/or 'double_center'")

                self.data_std_ = temp

    def cross_product(self):

        if self.hdf5 is not None:
            if 'cross_product' not in self.hdf5:
                self.hdf5.create_group('cross_product')

            if self.ID not in self.hdf5['cross_product'].keys():
                aff = self.data_std_.dot(self.data_std_.T)
                self.affinity_ = self.hdf5['cross_product'].create_dataset(self.ID, data=aff, compression='gzip')
                del aff
            else:
                self.affinity_ = self.hdf5['cross_product/%s' % self.ID]

        else:
            self.affinity_ = self.data_std_.dot(self.data_std_.T)

    def covariance(self):

        self.affinity_ = np.cov(self.data_std_, rowvar=False)

    def double_center(self):

        assert self.data_std_.shape[0] == self.data_std_.shape[
            1], "Error: double centering requires a square matrix (correlation or covariance)"

        cmat = np.eye(self.data_std_.shape[0]) - np.ones_like(self.data_std_) * data_std_.shape[0]
        self.affinity_ = 0.5 * cmat.dot(self.data_std_.dot(cmat))


class STATIS(object):
    def __init__(self, flavor='STATIS', n_comps = 30):
        """
        Initialize STATIS object
        :param flavor: Flavor of STATIS ('STATIS', 'ANISOSTATIS_C1', 'dualSTATIS', 'COVSTATIS')
        """

        self.data = None
        self.n_datasets = None
        self.n_observations = None
        self.inds_ = None
        self.flavor = flavor

        self.A_ = None
        self.M_ = None
        self.X_ = None

        self.P_ = None  # Left singular vectors
        self.Q_ = None  # Right singular vectors
        self.D_ = None  # Singular values

        self.n_comps = n_comps

        self.groups_ = None
        self.ugroups_ = None
        self.n_groupings_ = None

        self.factor_scores_ = None
        self.partial_factor_scores_ = None
        self.col_indices_ = None

        self.contrib_obs_ = None
        self.contrib_var_ = None
        self.contrib_dat_ = None
        self.partial_inertia_dat_ = None
        self.contrib_grp_ = None

        self.ve_ = None

    def fit(self, data):

        """
        Main method to run STATIS on input example_data. Input example_data must be a list of STATISData objects.
        
        :param data: List of STATISdata objects
        """

        import time

        t0 = time.time()
        self.n_datasets = len(data)
        self.n_observations = data[0].data.shape[0]

        # Pre-process
        if self.flavor is 'COVSTATIS':
            self.data = gen_affinity_input(data, type='double_center')
        elif self.flavor is 'dualSTATIS':
            self.data = gen_affinity_input(data, type='covariance')
        elif self.flavor is 'STATIS':
            self.data = gen_affinity_input(data, type='cross_product')
        else:
            self.data = data

        self.ids_ = get_ids(self.data)
        self.groups_, self.ugroups_, self.n_groupings_ = get_groups(self.data)

        # Get weights and prepare for GSVD
        self.M_ = get_M(self.n_observations)
        self.X_, self.X_scaled_ = stack_tables(self.data, self.n_datasets)

        if self.flavor is 'ANISOSTATIS_C1':
            self.table_weights_ = aniso_c1(self.X_, self.M_)
        else:
            self.table_weights_ = rv_pca(self.data, self.n_datasets)

        self.A_ = get_A(self.data, self.table_weights_, self.n_datasets, self.flavor)

        # Decompose
        self.P_, self.D_, self.Q_, self.ev_ = gsvd(self.X_, self.M_, self.A_, self.n_comps)

        # Get indices for each dataset
        self.col_indices_, self.grp_indices_ = get_col_indices(self.data, self.ids_, self.groups_, self.ugroups_)

        # Post-process
        self.factor_scores_ = calc_factor_scores(self.P_, self.D_)
        self.partial_factor_scores_ = calc_partial_factor_scores(self.X_scaled_, self.Q_, self.col_indices_)
        self.contrib_obs_ = calc_contrib_obs(self.factor_scores_, self.ev_, self.M_, self.D_, self.n_observations,
                                             self.n_comps)
        self.contrib_var_ = calc_contrib_var(self.X_, self.Q_, self.A_, self.n_comps)
        self.contrib_dat_ = calc_contrib_dat(self.contrib_var_, self.col_indices_, self.n_datasets, self.n_comps)
        self.partial_inertia_dat_ = calc_partial_interia_dat(self.contrib_dat_, self.ev_)

        print('STATIS finished successfully in %.3f seconds' % (time.time() - t0))

    def print_variance_explained(self):

        self.ve_ = np.round(np.power(self.D_, 2) / sum(np.power(self.D_, 2)), 5)

        print("===================================================================")
        print('Component   % var     % cumulative')
        print('===================================================================')
        for i, v in enumerate(self.ve_):
            print('%s         %.3f     %.3f' % (str(i + 1).zfill(3), v * 100, np.sum(self.ve_[0:i + 1]) * 100))
