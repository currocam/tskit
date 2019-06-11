# MIT License
#
# Copyright (c) 2018-2019 Tskit Developers
# Copyright (C) 2016 University of Oxford
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
Test cases for generalized statistic computation.
"""
import io
import unittest
import random
import collections
import itertools
import functools
import contextlib

import numpy as np
import numpy.testing as nt

import msprime

import tskit
import tskit.exceptions as exceptions
import tests.tsutil as tsutil
import tests.test_wright_fisher as wf


def naive_general_branch_stats(ts, W, f, windows=None, polarised=False):
    n, K = W.shape
    if n != ts.num_samples:
        raise ValueError("First dimension of W must be number of samples")
    # Hack to determine M
    M = len(f(W[0]))
    total = np.sum(W, axis=0)

    sigma = np.zeros((ts.num_trees, M))
    for tree in ts.trees():
        X = np.zeros((ts.num_nodes, K))
        X[ts.samples()] = W
        for u in tree.nodes(order="postorder"):
            for v in tree.children(u):
                X[u] += X[v]
        if polarised:
            s = sum(tree.branch_length(u) * f(X[u]) for u in tree.nodes())
        else:
            s = sum(
                tree.branch_length(u) * (f(X[u]) + f(total - X[u]))
                for u in tree.nodes())
        sigma[tree.index] = s * tree.span
    if windows is None:
        return sigma
    else:
        bsc = tskit.BranchLengthStatCalculator(ts)
        return bsc.windowed_tree_stat(sigma, windows)


def naive_general_site_stats(ts, W, f, windows=None, polarised=False):
    n, K = W.shape
    if n != ts.num_samples:
        raise ValueError("First dimension of W must be number of samples")
    # Hack to determine M
    M = len(f(W[0]))
    sigma = np.zeros((ts.num_sites, M))
    for tree in ts.trees():
        X = np.zeros((ts.num_nodes, K))
        X[ts.samples()] = W
        for u in tree.nodes(order="postorder"):
            for v in tree.children(u):
                X[u] += X[v]
        for site in tree.sites():
            state_map = collections.defaultdict(functools.partial(np.zeros, K))
            state_map[site.ancestral_state] = sum(X[root] for root in tree.roots)
            for mutation in site.mutations:
                state_map[mutation.derived_state] += X[mutation.node]
                if mutation.parent != tskit.NULL:
                    parent = site.mutations[mutation.parent - site.mutations[0].id]
                    state_map[parent.derived_state] -= X[mutation.node]
                else:
                    state_map[site.ancestral_state] -= X[mutation.node]
            if polarised:
                del state_map[site.ancestral_state]
            sigma[site.id] += sum(map(f, state_map.values()))
    if windows is None:
        return sigma
    else:
        ssc = tskit.SiteStatCalculator(ts)
        return ssc.windowed_sitewise_stat(sigma, windows)


def general_site_stats(ts, W, f, windows=None, polarised=False):
    # moved code over to tskit/stats.py
    ssc = tskit.SiteStatCalculator(ts)
    return ssc.general_stat(W, f, windows=windows, polarised=polarised)


def path_length(tr, x, y):
    L = 0
    if x >= 0 and y >= 0:
        mrca = tr.mrca(x, y)
    else:
        mrca = -1
    for u in x, y:
        while u != mrca:
            L += tr.branch_length(u)
            u = tr.parent(u)
    return L


@contextlib.contextmanager
def suppress_division_by_zero_warning():
    with np.errstate(invalid='ignore', divide='ignore'):
        yield


##############################
# Branch general stat algorithms
##############################

def windowed_tree_stat(ts, stat, windows, span_normalise=True):
    shape = list(stat.shape)
    shape[0] = len(windows) - 1
    A = np.zeros(shape)

    tree_breakpoints = np.array(list(ts.breakpoints()))
    tree_index = 0
    for j in range(len(windows) - 1):
        w_left = windows[j]
        w_right = windows[j + 1]
        while True:
            t_left = tree_breakpoints[tree_index]
            t_right = tree_breakpoints[tree_index + 1]
            left = max(t_left, w_left)
            right = min(t_right, w_right)
            weight = max(0.0, (right - left) / (t_right - t_left))
            A[j] += stat[tree_index] * weight
            assert left != right
            if t_right <= w_right:
                tree_index += 1
                # TODO This is inelegant - should include this in the case below
                if t_right == w_right:
                    break
            else:
                break
    if span_normalise:
        # re-normalize by window lengths
        window_lengths = np.diff(windows)
        for j in range(len(windows) - 1):
            A[j] /= window_lengths[j]
    return A


def naive_branch_general_stat(ts, w, f, windows=None, polarised=False,
                              span_normalise=True):
    if windows is None:
        windows = [0.0, ts.sequence_length]
    n, k = w.shape
    # hack to determine m
    m = len(f(w[0]))
    total = np.sum(w, axis=0)

    sigma = np.zeros((ts.num_trees, m))
    for tree in ts.trees():
        x = np.zeros((ts.num_nodes, k))
        x[ts.samples()] = w
        for u in tree.nodes(order="postorder"):
            for v in tree.children(u):
                x[u] += x[v]
        if polarised:
            s = sum(tree.branch_length(u) * f(x[u]) for u in tree.nodes())
        else:
            s = sum(
                tree.branch_length(u) * (f(x[u]) + f(total - x[u]))
                for u in tree.nodes())
        sigma[tree.index] = s * tree.span
    if isinstance(windows, str) and windows == "trees":
        # need to average across the windows
        if span_normalise:
            for j, tree in enumerate(ts.trees()):
                sigma[j] /= tree.span
        return sigma
    else:
        return windowed_tree_stat(ts, sigma, windows, span_normalise=span_normalise)


def branch_general_stat(ts, sample_weights, summary_func, windows=None,
                        polarised=False, span_normalise=True):
    """
    Efficient implementation of the algorithm used as the basis for the
    underlying C version.
    """
    n, state_dim = sample_weights.shape
    windows = ts.parse_windows(windows)
    num_windows = windows.shape[0] - 1

    # Determine result_dim
    result_dim = len(summary_func(sample_weights[0]))
    result = np.zeros((num_windows, result_dim))
    state = np.zeros((ts.num_nodes, state_dim))
    state[ts.samples()] = sample_weights
    total_weight = np.sum(sample_weights, axis=0)

    def area_weighted_summary(u):
        v = parent[u]
        branch_length = 0
        if v != -1:
            branch_length = time[v] - time[u]
        s = summary_func(state[u])
        if not polarised:
            s += summary_func(total_weight - state[u])
        return branch_length * s

    tree_index = 0
    window_index = 0
    time = ts.tables.nodes.time
    parent = np.zeros(ts.num_nodes, dtype=np.int32) - 1
    running_sum = np.zeros(result_dim)
    for (t_left, t_right), edges_out, edges_in in ts.edge_diffs():
        for edge in edges_out:
            u = edge.child
            running_sum -= area_weighted_summary(u)
            u = edge.parent
            while u != -1:
                running_sum -= area_weighted_summary(u)
                state[u] -= state[edge.child]
                running_sum += area_weighted_summary(u)
                u = parent[u]
            parent[edge.child] = -1

        for edge in edges_in:
            parent[edge.child] = edge.parent
            u = edge.child
            running_sum += area_weighted_summary(u)
            u = edge.parent
            while u != -1:
                running_sum -= area_weighted_summary(u)
                state[u] += state[edge.child]
                running_sum += area_weighted_summary(u)
                u = parent[u]

        # Update the windows
        assert window_index < num_windows
        while windows[window_index] < t_right:
            w_left = windows[window_index]
            w_right = windows[window_index + 1]
            left = max(t_left, w_left)
            right = min(t_right, w_right)
            weight = right - left
            assert weight > 0
            result[window_index] += running_sum * weight
            if w_right <= t_right:
                window_index += 1
            else:
                # This interval crosses a tree boundary, so we update it again in the
                # for the next tree
                break

        tree_index += 1

    # print("window_index:", window_index, windows.shape)
    assert window_index == windows.shape[0] - 1
    if span_normalise:
        for j in range(num_windows):
            result[j] /= windows[j + 1] - windows[j]
    return result


##############################
# Site general stat algorithms
##############################

def windowed_sitewise_stat(ts, sigma, windows, span_normalise=True):
    M = sigma.shape[1]
    A = np.zeros((len(windows) - 1, M))
    window = 0
    for site in ts.sites():
        while windows[window + 1] <= site.position:
            window += 1
        assert windows[window] <= site.position < windows[window + 1]
        A[window] += sigma[site.id]
    if span_normalise:
        diff = np.zeros((A.shape[0], 1))
        diff[:, 0] = np.diff(windows).T
        A /= diff
    return A


def naive_site_general_stat(ts, W, f, windows=None, polarised=False,
                            span_normalise=True):
    n, K = W.shape
    # Hack to determine M
    M = len(f(W[0]))
    sigma = np.zeros((ts.num_sites, M))
    for tree in ts.trees():
        X = np.zeros((ts.num_nodes, K))
        X[ts.samples()] = W
        for u in tree.nodes(order="postorder"):
            for v in tree.children(u):
                X[u] += X[v]
        for site in tree.sites():
            state_map = collections.defaultdict(functools.partial(np.zeros, K))
            state_map[site.ancestral_state] = sum(X[root] for root in tree.roots)
            for mutation in site.mutations:
                state_map[mutation.derived_state] += X[mutation.node]
                if mutation.parent != tskit.NULL:
                    parent = site.mutations[mutation.parent - site.mutations[0].id]
                    state_map[parent.derived_state] -= X[mutation.node]
                else:
                    state_map[site.ancestral_state] -= X[mutation.node]
            if polarised:
                del state_map[site.ancestral_state]
            sigma[site.id] += sum(map(f, state_map.values()))
    return windowed_sitewise_stat(
        ts, sigma, ts.parse_windows(windows),
        span_normalise=span_normalise)


def site_general_stat(ts, sample_weights, summary_func, windows=None, polarised=False,
                      span_normalise=True):
    """
    Problem: 'sites' is different that the other windowing options
    because if we output by site we don't want to normalize by length of the window.
    Solution: we pass an argument "normalize", to the windowing function.
    """
    windows = ts.parse_windows(windows)
    num_windows = windows.shape[0] - 1
    n, state_dim = sample_weights.shape
    # Determine result_dim
    result_dim, = summary_func(sample_weights[0]).shape
    result = np.zeros((num_windows, result_dim))
    state = np.zeros((ts.num_nodes, state_dim))
    state[ts.samples()] = sample_weights
    total_weight = np.sum(sample_weights, axis=0)

    site_index = 0
    mutation_index = 0
    window_index = 0
    sites = ts.tables.sites
    mutations = ts.tables.mutations
    parent = np.zeros(ts.num_nodes, dtype=np.int32) - 1
    for (left, right), edges_out, edges_in in ts.edge_diffs():
        for edge in edges_out:
            u = edge.parent
            while u != -1:
                state[u] -= state[edge.child]
                u = parent[u]
            parent[edge.child] = -1
        for edge in edges_in:
            parent[edge.child] = edge.parent
            u = edge.parent
            while u != -1:
                state[u] += state[edge.child]
                u = parent[u]
        while site_index < len(sites) and sites.position[site_index] < right:
            assert left <= sites.position[site_index]
            ancestral_state = sites[site_index].ancestral_state
            allele_state = collections.defaultdict(
                functools.partial(np.zeros, state_dim))
            allele_state[ancestral_state][:] = total_weight
            while (
                    mutation_index < len(mutations)
                    and mutations[mutation_index].site == site_index):
                mutation = mutations[mutation_index]
                allele_state[mutation.derived_state] += state[mutation.node]
                if mutation.parent != -1:
                    parent_allele = mutations[mutation.parent].derived_state
                    allele_state[parent_allele] -= state[mutation.node]
                else:
                    allele_state[ancestral_state] -= state[mutation.node]
                mutation_index += 1
            if polarised:
                del allele_state[ancestral_state]
            site_result = np.zeros(result_dim)
            for allele, value in allele_state.items():
                site_result += summary_func(value)

            pos = sites.position[site_index]
            while windows[window_index + 1] <= pos:
                window_index += 1
            assert windows[window_index] <= pos < windows[window_index + 1]
            result[window_index] += site_result
            site_index += 1
    if span_normalise:
        for j in range(num_windows):
            span = windows[j + 1] - windows[j]
            result[j] /= span
    return result


##############################
# Node general stat algorithms
##############################


def naive_node_general_stat(ts, W, f, windows=None, polarised=False,
                            span_normalise=True):
    windows = ts.parse_windows(windows)
    n, K = W.shape
    M = f(W[0]).shape[0]
    total = np.sum(W, axis=0)
    sigma = np.zeros((ts.num_trees, ts.num_nodes, M))
    for tree in ts.trees():
        X = np.zeros((ts.num_nodes, K))
        X[ts.samples()] = W
        for u in tree.nodes(order="postorder"):
            for v in tree.children(u):
                X[u] += X[v]
        s = np.zeros((ts.num_nodes, M))
        for u in range(ts.num_nodes):
            s[u] = f(X[u])
            if not polarised:
                s[u] += f(total - X[u])
        sigma[tree.index] = s * tree.span
    return windowed_tree_stat(ts, sigma, windows, span_normalise=span_normalise)


def node_general_stat(ts, sample_weights, summary_func, windows=None, polarised=False,
                      span_normalise=True):
    """
    Efficient implementation of the algorithm used as the basis for the
    underlying C version.
    """
    n, state_dim = sample_weights.shape
    windows = ts.parse_windows(windows)
    num_windows = windows.shape[0] - 1
    result_dim = summary_func(sample_weights[0]).shape[0]
    result = np.zeros((num_windows, ts.num_nodes, result_dim))
    state = np.zeros((ts.num_nodes, state_dim))
    state[ts.samples()] = sample_weights
    total_weight = np.sum(sample_weights, axis=0)

    def node_summary(u):
        s = summary_func(state[u])
        if not polarised:
            s += summary_func(total_weight - state[u])
        return s

    tree_index = 0
    window_index = 0
    parent = np.zeros(ts.num_nodes, dtype=np.int32) - 1
    # contains summary_func(state[u]) for each node
    current_values = np.zeros((ts.num_nodes, result_dim))
    for u in ts.samples():
        current_values[u] = node_summary(u)
    # contains the location of the last time we updated the output for a node.
    last_update = np.zeros((ts.num_nodes, 1))
    for (t_left, t_right), edges_out, edges_in in ts.edge_diffs():

        for edge in edges_out:
            u = edge.child
            v = edge.parent
            while v != -1:
                result[window_index, v] += (t_left - last_update[v]) * current_values[v]
                last_update[v] = t_left
                state[v] -= state[u]
                current_values[v] = node_summary(v)
                v = parent[v]
            parent[u] = -1

        for edge in edges_in:
            u = edge.child
            v = edge.parent
            parent[u] = v
            while v != -1:
                result[window_index, v] += (t_left - last_update[v]) * current_values[v]
                last_update[v] = t_left
                state[v] += state[u]
                current_values[v] = node_summary(v)
                v = parent[v]

        # Update the windows
        while window_index < num_windows and windows[window_index + 1] <= t_right:
            w_right = windows[window_index + 1]
            # Flush the contribution of all nodes to the current window.
            for u in range(ts.num_nodes):
                result[window_index, u] += (w_right - last_update[u]) * current_values[u]
                last_update[u] = w_right
            window_index += 1
        tree_index += 1

    assert window_index == windows.shape[0] - 1
    if span_normalise:
        for j in range(num_windows):
            result[j] /= windows[j + 1] - windows[j]
    return result


def general_stat(
        ts, sample_weights, summary_func, windows=None, polarised=False,
        mode="site", span_normalise=True):
    """
    General iterface for algorithms above. Directly corresponds to the interface
    for TreeSequence.general_stat.
    """
    method_map = {
        "site": site_general_stat,
        "node": node_general_stat,
        "branch": branch_general_stat}
    return method_map[mode](
        ts, sample_weights, summary_func, windows=windows, polarised=polarised,
        span_normalise=span_normalise)


def upper_tri_to_matrix(x):
    """
    Given x, a vector of entries of the upper triangle of a matrix
    in row-major order, including the diagonal, return the corresponding matrix.
    """
    # n^2 + n = 2 u => n = (-1 + sqrt(1 + 8*u))/2
    n = int((np.sqrt(1 + 8 * len(x)) - 1)/2.0)
    out = np.ones((n, n))
    k = 0
    for i in range(n):
        for j in range(i, n):
            out[i, j] = out[j, i] = x[k]
            k += 1
    return out


##################################
# Test cases
##################################


class StatsTestCase(unittest.TestCase):
    """
    Provides convenience functions.
    """
    def assertListAlmostEqual(self, x, y):
        self.assertEqual(len(x), len(y))
        for a, b in zip(x, y):
            self.assertAlmostEqual(a, b)

    def assertArrayEqual(self, x, y):
        nt.assert_equal(x, y)

    def assertArrayAlmostEqual(self, x, y):
        nt.assert_array_almost_equal(x, y)


class TopologyExamplesMixin(object):
    """
    Defines a set of test cases on different example tree sequence topologies.
    Derived classes need to define a 'verify' function which will perform the
    actual tests.
    """
    def test_single_tree(self):
        ts = msprime.simulate(6, random_seed=1)
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_many_trees(self):
        ts = msprime.simulate(6, recombination_rate=2, random_seed=1)
        self.assertGreater(ts.num_trees, 2)
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_many_trees_sequence_length(self):
        for L in [0.5, 1.5, 3.3333]:
            ts = msprime.simulate(6, length=L, recombination_rate=2, random_seed=1)
            self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_unsimplified(self):
        tables = wf.wf_sim(
            4, 5, seed=1, deep_history=True, initial_generation_samples=False,
            num_loci=5)
        tables.sort()
        ts = tables.tree_sequence()
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_initial_generation(self):
        tables = wf.wf_sim(
            6, 5, seed=3, deep_history=True, initial_generation_samples=True,
            num_loci=2)
        tables.sort()
        tables.simplify()
        ts = tables.tree_sequence()
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_initial_generation_no_deep_history(self):
        tables = wf.wf_sim(
            6, 15, seed=202, deep_history=False, initial_generation_samples=True,
            num_loci=5)
        tables.sort()
        tables.simplify()
        ts = tables.tree_sequence()
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_unsimplified_multiple_roots(self):
        tables = wf.wf_sim(
            5, 8, seed=1, deep_history=False, initial_generation_samples=False,
            num_loci=4)
        tables.sort()
        ts = tables.tree_sequence()
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_simplified(self):
        tables = wf.wf_sim(
            5, 8, seed=1, deep_history=True, initial_generation_samples=False,
            num_loci=5)
        tables.sort()
        ts = tables.tree_sequence().simplify()
        self.verify(ts)

    @unittest.skip("inconsistent nan issues")
    def test_wright_fisher_simplified_multiple_roots(self):
        tables = wf.wf_sim(
            6, 10, seed=1, deep_history=False, initial_generation_samples=False,
            num_loci=3)
        tables.sort()
        ts = tables.tree_sequence()
        self.verify(ts)

    @unittest.skip("Incorrect semantics on empty ts; #207")
    def test_empty_ts(self):
        tables = tskit.TableCollection(1.0)
        tables.nodes.add_row(1, 0)
        tables.nodes.add_row(1, 0)
        ts = tables.tree_sequence()
        self.verify(ts)


class MutatedTopologyExamplesMixin(object):
    """
    Defines a set of test cases on different example tree sequence topologies.
    Derived classes need to define a 'verify' function which will perform the
    actual tests.
    """
    def test_single_tree_no_sites(self):
        ts = msprime.simulate(6, random_seed=1)
        self.assertEqual(ts.num_sites, 0)
        self.verify(ts)

    def test_single_tree_infinite_sites(self):
        ts = msprime.simulate(6, random_seed=1, mutation_rate=1)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_single_tree_sites_no_mutations(self):
        ts = msprime.simulate(6, random_seed=1)
        tables = ts.dump_tables()
        tables.sites.add_row(0.1, "a")
        tables.sites.add_row(0.2, "aaa")
        self.verify(tables.tree_sequence())

    def test_single_tree_jukes_cantor(self):
        ts = msprime.simulate(6, random_seed=1, mutation_rate=1)
        ts = tsutil.jukes_cantor(ts, 20, 1, seed=10)
        self.verify(ts)

    def test_single_tree_multichar_mutations(self):
        ts = msprime.simulate(6, random_seed=1, mutation_rate=1)
        ts = tsutil.insert_multichar_mutations(ts)
        self.verify(ts)

    def test_many_trees_infinite_sites(self):
        ts = msprime.simulate(6, recombination_rate=2, mutation_rate=2, random_seed=1)
        self.assertGreater(ts.num_sites, 0)
        self.assertGreater(ts.num_trees, 2)
        self.verify(ts)

    def test_many_trees_sequence_length_infinite_sites(self):
        for L in [0.5, 1.5, 3.3333]:
            ts = msprime.simulate(
                6, length=L, recombination_rate=2, mutation_rate=1, random_seed=1)
            self.verify(ts)

    def test_wright_fisher_unsimplified(self):
        tables = wf.wf_sim(
            4, 5, seed=1, deep_history=True, initial_generation_samples=False,
            num_loci=10)
        tables.sort()
        ts = msprime.mutate(tables.tree_sequence(), rate=0.05, random_seed=234)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_wright_fisher_initial_generation(self):
        tables = wf.wf_sim(
            6, 5, seed=3, deep_history=True, initial_generation_samples=True,
            num_loci=2)
        tables.sort()
        tables.simplify()
        ts = msprime.mutate(tables.tree_sequence(), rate=0.08, random_seed=2)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_wright_fisher_initial_generation_no_deep_history(self):
        tables = wf.wf_sim(
            7, 15, seed=202, deep_history=False, initial_generation_samples=True,
            num_loci=5)
        tables.sort()
        tables.simplify()
        ts = msprime.mutate(tables.tree_sequence(), rate=0.01, random_seed=2)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_wright_fisher_unsimplified_multiple_roots(self):
        tables = wf.wf_sim(
            8, 15, seed=1, deep_history=False, initial_generation_samples=False,
            num_loci=20)
        tables.sort()
        ts = msprime.mutate(tables.tree_sequence(), rate=0.006, random_seed=2)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_wright_fisher_simplified(self):
        tables = wf.wf_sim(
            9, 10, seed=1, deep_history=True, initial_generation_samples=False,
            num_loci=5)
        tables.sort()
        ts = tables.tree_sequence().simplify()
        ts = msprime.mutate(ts, rate=0.01, random_seed=1234)
        self.assertGreater(ts.num_sites, 0)
        self.verify(ts)

    def test_empty_ts(self):
        tables = tskit.TableCollection(1.0)
        for _ in range(10):
            tables.nodes.add_row(tskit.NODE_IS_SAMPLE, 0)
        ts = tables.tree_sequence()
        self.verify(ts)


def example_sample_sets(ts, min_size=1):
    """
    Generate a series of example sample sets from the specfied tree sequence.
    """
    samples = ts.samples()
    yield [[u] for u in samples]
    splits = np.array_split(samples, min_size)
    yield splits
    yield splits[::-1]


def example_sample_set_index_pairs(sample_sets):
    k = len(sample_sets)
    assert k > 1
    yield [(0, 1)]
    yield [(1, 0), (0, 1)]
    if k > 2:
        yield [(0, 1), (1, 2), (0, 2)]


def example_sample_set_index_triples(sample_sets):
    k = len(sample_sets)
    assert k > 2
    yield [(0, 1, 2)]
    yield [(0, 2, 1), (2, 1, 0)]
    if k > 3:
        yield [(3, 0, 1), (0, 2, 3), (1, 2, 3)]


def example_sample_set_index_quads(sample_sets):
    k = len(sample_sets)
    assert k > 3
    yield [(0, 1, 2, 3)]
    yield [(0, 1, 2, 3), (3, 2, 1, 0)]
    yield [(0, 1, 2, 3), (3, 2, 1, 0), (1, 2, 3, 0)]


def example_windows(ts):
    """
    Generate a series of example windows for the specified tree sequence.
    """
    L = ts.sequence_length
    yield [0, L]
    yield ts.breakpoints(as_array=True)
    yield np.linspace(0, L, num=10)
    yield np.linspace(0, L, num=100)


class SampleSetStatsMixin(object):
    """
    Implements the verify method and dispatches it to verify_sample_sets
    for a representative set of sample sets and windows.
    """
    def verify(self, ts):
        for sample_sets, windows in itertools.product(
                example_sample_sets(ts), example_windows(ts)):
            self.verify_sample_sets(ts, sample_sets, windows=windows)

    def verify_definition(
            self, ts, sample_sets, windows, summary_func, ts_method, definition):

        W = np.array(
            [[u in A for A in sample_sets] for u in ts.samples()], dtype=float)

        def wrapped_summary_func(x):
            with suppress_division_by_zero_warning():
                return summary_func(x)

        for sn in [True, False]:
            sigma1 = ts.general_stat(W, wrapped_summary_func, windows, mode=self.mode,
                                     span_normalise=sn)
            sigma2 = general_stat(ts, W, wrapped_summary_func, windows, mode=self.mode,
                                  span_normalise=sn)
            sigma3 = ts_method(sample_sets, windows=windows, mode=self.mode,
                               span_normalise=sn)
            sigma4 = definition(ts, sample_sets, windows=windows, mode=self.mode,
                                span_normalise=sn)

            self.assertEqual(sigma1.shape, sigma2.shape)
            self.assertEqual(sigma1.shape, sigma3.shape)
            self.assertEqual(sigma1.shape, sigma4.shape)
            self.assertArrayAlmostEqual(sigma1, sigma2)
            self.assertArrayAlmostEqual(sigma1, sigma3)
            self.assertArrayAlmostEqual(sigma1, sigma4)


class KWaySampleSetStatsMixin(SampleSetStatsMixin):
    """
    Defines the verify definition method, which comparse the results from
    several different ways of defining and computing the same statistic.
    """
    def verify_definition(
            self, ts, sample_sets, indexes, windows, summary_func, ts_method,
            definition):

        def wrapped_summary_func(x):
            with suppress_division_by_zero_warning():
                return summary_func(x)

        W = np.array(
            [[u in A for A in sample_sets] for u in ts.samples()], dtype=float)
        sigma1 = ts.general_stat(W, wrapped_summary_func, windows, mode=self.mode)
        sigma2 = general_stat(ts, W, wrapped_summary_func, windows, mode=self.mode)
        sigma3 = ts_method(
            sample_sets, indexes=indexes, windows=windows, mode=self.mode)
        sigma4 = definition(
            ts, sample_sets, indexes=indexes, windows=windows, mode=self.mode)

        self.assertEqual(sigma1.shape, sigma2.shape)
        self.assertEqual(sigma1.shape, sigma3.shape)
        self.assertEqual(sigma1.shape, sigma4.shape)
        self.assertArrayAlmostEqual(sigma1, sigma2)
        self.assertArrayAlmostEqual(sigma1, sigma3)
        self.assertArrayAlmostEqual(sigma1, sigma4)


class TwoWaySampleSetStatsMixin(KWaySampleSetStatsMixin):
    """
    Implements the verify method and dispatches it to verify_sample_sets_indexes,
    which gives a representative sample of sample set indexes.
    """

    def verify(self, ts):
        for sample_sets, windows in itertools.product(
                example_sample_sets(ts, min_size=2), example_windows(ts)):
            for indexes in example_sample_set_index_pairs(sample_sets):
                self.verify_sample_sets_indexes(ts, sample_sets, indexes, windows)


class ThreeWaySampleSetStatsMixin(KWaySampleSetStatsMixin):
    """
    Implements the verify method and dispatches it to verify_sample_sets_indexes,
    which gives a representative sample of sample set indexes.
    """
    def verify(self, ts):
        for sample_sets, windows in itertools.product(
                example_sample_sets(ts, min_size=3), example_windows(ts)):
            for indexes in example_sample_set_index_triples(sample_sets):
                self.verify_sample_sets_indexes(ts, sample_sets, indexes, windows)


class FourWaySampleSetStatsMixin(KWaySampleSetStatsMixin):
    """
    Implements the verify method and dispatches it to verify_sample_sets_indexes,
    which gives a representative sample of sample set indexes.
    """
    def verify(self, ts):
        for sample_sets, windows in itertools.product(
                example_sample_sets(ts, min_size=4), example_windows(ts)):
            for indexes in example_sample_set_index_quads(sample_sets):
                self.verify_sample_sets_indexes(ts, sample_sets, indexes, windows)


############################################
# Diversity
############################################


def site_diversity(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(sample_sets)))
    samples = ts.samples()
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        haps = ts.genotype_matrix().T
        site_positions = [x.position for x in ts.sites()]
        for i, X in enumerate(sample_sets):
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for x in X:
                        for y in set(X) - set([x]):
                            x_index = np.where(samples == x)[0][0]
                            y_index = np.where(samples == y)[0][0]
                            if haps[x_index][k] != haps[y_index][k]:
                                # x|y
                                S += 1
            if site_in_window:
                denom = len(X) * (len(X) - 1)
                if span_normalise:
                    denom *= end - begin
                denom = np.array(denom)
                with suppress_division_by_zero_warning():
                    out[j][i] = S / denom
    return out


def branch_diversity(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(sample_sets)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, X in enumerate(sample_sets):
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                SS = 0
                for x in X:
                    for y in set(X) - set([x]):
                        SS += path_length(tr, x, y)
                S += SS*(min(end, tr.interval[1]) - max(begin, tr.interval[0]))
            denom = len(X) * (len(X) - 1)
            if span_normalise:
                denom *= end - begin
            with suppress_division_by_zero_warning():
                out[j][i] = S / denom
    return out


def node_diversity(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    K = len(sample_sets)
    out = np.zeros((len(windows) - 1, ts.num_nodes, K))
    for k in range(K):
        X = sample_sets[k]
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            tX = len(X)
            S = np.zeros(ts.num_nodes)
            for tr in ts.trees(tracked_samples=X):
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in tr.nodes():
                    # count number of pairwise paths going through u
                    n = tr.num_tracked_samples(u)
                    SS[u] += n * (tX - n)
                S += SS*(min(end, tr.interval[1]) - max(begin, tr.interval[0]))
            denom = len(X) * (len(X) - 1)
            if span_normalise:
                denom *= end - begin
            with suppress_division_by_zero_warning():
                out[j, :, k] = 2 * S / denom
    return out


def diversity(ts, sample_sets, windows=None, mode="site", span_normalise=True):
    """
    Computes average pairwise diversity between two random choices from x
    over the window specified.
    """
    method_map = {
        "site": site_diversity,
        "node": node_diversity,
        "branch": branch_diversity}
    return method_map[mode](ts, sample_sets, windows=windows,
                            span_normalise=span_normalise)


class TestDiversity(StatsTestCase, SampleSetStatsMixin):
    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets(self, ts, sample_sets, windows):
        # print("verify", ts, sample_sets, windows)
        n = np.array([len(x) for x in sample_sets])

        def f(x):
            return x * (n - x) / (n * (n - 1))

        self.verify_definition(
            ts, sample_sets, windows, f, ts.diversity, diversity)


class TestBranchDiversity(TestDiversity, TopologyExamplesMixin):
    mode = "branch"


class TestNodeDiversity(TestDiversity, TopologyExamplesMixin):
    mode = "node"


class TestSiteDiversity(TestDiversity, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Y1
############################################

def branch_Y1(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(sample_sets)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, X in enumerate(sample_sets):
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                for x in X:
                    for y in set(X) - {x}:
                        for z in set(X) - {x, y}:
                            xy_mrca = tr.mrca(x, y)
                            xz_mrca = tr.mrca(x, z)
                            yz_mrca = tr.mrca(y, z)
                            if xy_mrca == xz_mrca:
                                #   /\
                                #  / /\
                                # x y  z
                                S += path_length(tr, x, yz_mrca) * this_length
                            elif xy_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # y x  z
                                S += path_length(tr, x, xz_mrca) * this_length
                            elif xz_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # z x  y
                                S += path_length(tr, x, xy_mrca) * this_length
            denom = len(X) * (len(X)-1) * (len(X)-2)
            if span_normalise:
                denom *= (end - begin)
            # Make sure that divisiion is done by numpy so that we can handle
            # division by zero.
            denom = np.array(denom)
            with suppress_division_by_zero_warning():
                out[j][i] = S / denom
    return out


def site_Y1(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(sample_sets)))
    samples = ts.samples()
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        haps = ts.genotype_matrix().T
        site_positions = [x.position for x in ts.sites()]
        for i, X in enumerate(sample_sets):
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for x in X:
                        x_index = np.where(samples == x)[0][0]
                        for y in set(X) - {x}:
                            y_index = np.where(samples == y)[0][0]
                            for z in set(X) - {x, y}:
                                z_index = np.where(samples == z)[0][0]
                                condition = (
                                    haps[x_index, k] != haps[y_index, k] and
                                    haps[x_index, k] != haps[z_index, k])
                                if condition:
                                    # x|yz
                                    S += 1
            if site_in_window:
                denom = len(X) * (len(X)-1) * (len(X)-2)
                if span_normalise:
                    denom *= (end - begin)
                # Make sure division is done by numpy
                denom = np.array(denom)
                with suppress_division_by_zero_warning():
                    out[j][i] = S / denom
    return out


def node_Y1(ts, sample_sets, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    K = len(sample_sets)
    out = np.zeros((len(windows) - 1, ts.num_nodes, K))
    for k in range(K):
        X = sample_sets[k]
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            tX = len(X)
            S = np.zeros(ts.num_nodes)
            for tr in ts.trees(tracked_samples=X):
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in tr.nodes():
                    # count number of paths above a but not b,c
                    n = tr.num_tracked_samples(u)
                    SS[u] += (n * (tX - n) * (tX - n - 1) + (tX - n) * n * (n - 1))
                S += SS*(min(end, tr.interval[1]) - max(begin, tr.interval[0]))
            denom = tX * (tX - 1) * (tX - 2)
            if span_normalise:
                denom *= end - begin
            with suppress_division_by_zero_warning():
                out[j, :, k] = S / denom
    return out


def Y1(ts, sample_sets, windows=None, mode="site", span_normalise=True):
    windows = ts.parse_windows(windows)
    method_map = {
        "site": site_Y1,
        "node": node_Y1,
        "branch": branch_Y1}
    return method_map[mode](ts, sample_sets, windows=windows,
                            span_normalise=span_normalise)


class TestY1(StatsTestCase, SampleSetStatsMixin):
    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets(self, ts, sample_sets, windows):
        n = np.array([len(x) for x in sample_sets])
        denom = n * (n - 1) * (n - 2)

        def f(x):
            return x * (n - x) * (n - x - 1) / denom

        self.verify_definition(ts, sample_sets, windows, f, ts.Y1, Y1)


class TestBranchY1(TestY1, TopologyExamplesMixin):
    mode = "branch"


class TestNodeY1(TestY1, TopologyExamplesMixin):
    mode = "node"


class TestSiteY1(TestY1, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Divergence
############################################

def site_divergence(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, len(indexes)))
    samples = ts.samples()
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        haps = ts.genotype_matrix().T
        site_positions = [x.position for x in ts.sites()]
        for i, (ix, iy) in enumerate(indexes):
            X = sample_sets[ix]
            Y = sample_sets[iy]
            S = 0
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    for x in X:
                        x_index = np.where(samples == x)[0][0]
                        for y in Y:
                            y_index = np.where(samples == y)[0][0]
                            if haps[x_index][k] != haps[y_index][k]:
                                # x|y
                                S += 1
            denom = len(X) * len(Y)
            if span_normalise:
                denom *= (end - begin)
            out[j][i] = S / denom
    return out


def branch_divergence(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ix, iy) in enumerate(indexes):
            X = sample_sets[ix]
            Y = sample_sets[iy]
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                SS = 0
                for x in X:
                    for y in Y:
                        SS += path_length(tr, x, y)
                S += SS*(min(end, tr.interval[1]) - max(begin, tr.interval[0]))
            denom = len(X) * len(Y)
            if span_normalise:
                denom *= (end - begin)
            out[j][i] = S / denom
    return out


def node_divergence(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (ix, iy) in enumerate(indexes):
        X = sample_sets[ix]
        Y = sample_sets[iy]
        tX = len(X)
        tY = len(Y)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2 in zip(ts.trees(tracked_samples=X),
                              ts.trees(tracked_samples=Y)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nX = t1.num_tracked_samples(u)
                    nY = t2.num_tracked_samples(u)
                    SS[u] += nX * (tY - nY) + (tX - nX) * nY
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(X) * len(Y)
            if span_normalise:
                denom *= (end - begin)
            out[j, :, i] = S / denom
    return out


def divergence(ts, sample_sets, indexes=None, windows=None, mode="site",
               span_normalise=True):
    """
    Computes average pairwise divergence between two random choices from x
    over the window specified.
    """
    windows = ts.parse_windows(windows)
    if indexes is None:
        indexes = [(0, 1)]
    method_map = {
        "site": site_divergence,
        "node": node_divergence,
        "branch": branch_divergence}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class TestDivergence(StatsTestCase, TwoWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        # print("verify_indexes", ts, sample_sets, indexes, windows)
        n = np.array([len(x) for x in sample_sets])

        denom = np.array([n[i] * (n[j] - (i == j)) for i, j in indexes])

        def f(x):
            numer = np.array([(x[i] * (n[j] - x[j])) for i, j in indexes])
            return numer / denom

        self.verify_definition(
            ts, sample_sets, indexes, windows, f, ts.divergence, divergence)


class TestBranchDivergence(TestDivergence, TopologyExamplesMixin):
    mode = "branch"


class TestNodeDivergence(TestDivergence, TopologyExamplesMixin):
    mode = "node"


class TestSiteDivergence(TestDivergence, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Fst
############################################

def single_site_Fst(ts, sample_sets, indexes):
    """
    Compute single-site Fst, which between two groups with frequencies p and q is
      1 - 2 * (p (1-p) + q(1-q)) / ( p(1-p) + q(1-q) + p(1-q) + q(1-p) )
    or in the multiallelic case, replacing p(1-p) with the sum over alleles of p(1-p),
    and adjusted for sampling without replacement.
    """
    # TODO: what to do in this case?
    if ts.num_sites == 0:
        out = np.array([np.repeat(np.nan, len(indexes))])
        return out
    out = np.zeros((ts.num_sites, len(indexes)))
    samples = ts.samples()
    for j, v in enumerate(ts.variants()):
        for i, (ix, iy) in enumerate(indexes):
            g = v.genotypes
            X = sample_sets[ix]
            Y = sample_sets[iy]
            gX = [a for k, a in zip(samples, g) if k in X]
            gY = [a for k, a in zip(samples, g) if k in Y]
            nX = len(X)
            nY = len(Y)
            dX = dY = dXY = 0
            for a in set(g):
                fX = np.sum(gX == a)
                fY = np.sum(gY == a)
                with suppress_division_by_zero_warning():
                    dX += fX * (nX - fX) / (nX * (nX - 1))
                    dY += fY * (nY - fY) / (nY * (nY - 1))
                    dXY += (fX * (nY - fY) + (nX - fX) * fY) / (2 * nX * nY)
            with suppress_division_by_zero_warning():
                out[j][i] = 1 - 2 * (dX + dY) / (dX + dY + 2 * dXY)
    return out


class TestFst(StatsTestCase, TwoWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify(self, ts):
        # only check per-site
        for sample_sets in example_sample_sets(ts, min_size=2):
            for indexes in example_sample_set_index_pairs(sample_sets):
                self.verify_persite_Fst(ts, sample_sets, indexes)

    def verify_persite_Fst(self, ts, sample_sets, indexes):
        sigma1 = ts.Fst(sample_sets, indexes=indexes, windows="sites",
                        mode=self.mode, span_normalise=False)
        sigma2 = single_site_Fst(ts, sample_sets, indexes)
        self.assertEqual(sigma1.shape, sigma2.shape)
        self.assertArrayAlmostEqual(sigma1, sigma2)


class TestSiteFst(TestFst, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Y2
############################################

def branch_Y2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ix, iy) in enumerate(indexes):
            X = sample_sets[ix]
            Y = sample_sets[iy]
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                for x in X:
                    for y in Y:
                        for z in set(Y) - {y}:
                            xy_mrca = tr.mrca(x, y)
                            xz_mrca = tr.mrca(x, z)
                            yz_mrca = tr.mrca(y, z)
                            if xy_mrca == xz_mrca:
                                #   /\
                                #  / /\
                                # x y  z
                                S += path_length(tr, x, yz_mrca) * this_length
                            elif xy_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # y x  z
                                S += path_length(tr, x, xz_mrca) * this_length
                            elif xz_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # z x  y
                                S += path_length(tr, x, xy_mrca) * this_length
            denom = len(X) * len(Y) * (len(Y)-1)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j][i] = S / denom
    return out


def site_Y2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    samples = ts.samples()
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        haps = ts.genotype_matrix().T
        site_positions = [x.position for x in ts.sites()]
        for i, (ix, iy) in enumerate(indexes):
            X = sample_sets[ix]
            Y = sample_sets[iy]
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for x in X:
                        x_index = np.where(samples == x)[0][0]
                        for y in Y:
                            y_index = np.where(samples == y)[0][0]
                            for z in set(Y) - {y}:
                                z_index = np.where(samples == z)[0][0]
                                condition = (
                                    haps[x_index, k] != haps[y_index, k] and
                                    haps[x_index, k] != haps[z_index, k])
                                if condition:
                                    # x|yz
                                    S += 1
            if site_in_window:
                denom = len(X) * len(Y) * (len(Y)-1)
                if span_normalise:
                    denom *= (end - begin)
                with suppress_division_by_zero_warning():
                    out[j][i] = S / denom
    return out


def node_Y2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (ix, iy) in enumerate(indexes):
        X = sample_sets[ix]
        Y = sample_sets[iy]
        tX = len(X)
        tY = len(Y)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2 in zip(ts.trees(tracked_samples=X),
                              ts.trees(tracked_samples=Y)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nX = t1.num_tracked_samples(u)
                    nY = t2.num_tracked_samples(u)
                    SS[u] += nX * (tY - nY) * (tY - nY - 1) + (tX - nX) * nY * (nY - 1)
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(X) * len(Y) * (len(Y) - 1)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j, :, i] = S / denom
    return out


def Y2(ts, sample_sets, indexes=None, windows=None, mode="site", span_normalise=True):
    windows = ts.parse_windows(windows)

    windows = ts.parse_windows(windows)
    if indexes is None:
        indexes = [(0, 1)]
    method_map = {
        "site": site_Y2,
        "node": node_Y2,
        "branch": branch_Y2}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class TestY2(StatsTestCase, TwoWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        n = np.array([len(x) for x in sample_sets])

        denom = np.array([n[i] * n[j] * (n[j] - 1) for i, j in indexes])

        def f(x):
            numer = np.array([
                (x[i] * (n[j] - x[j]) * (n[j] - x[j] - 1)) for i, j in indexes])
            return numer / denom

        self.verify_definition(ts, sample_sets, indexes, windows, f, ts.Y2, Y2)


class TestBranchY2(TestY2, TopologyExamplesMixin):
    mode = "branch"


class TestNodeY2(TestY2, TopologyExamplesMixin):
    mode = "node"


class TestSiteY2(TestY2, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Y3
############################################

def branch_Y3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ix, iy, iz) in enumerate(indexes):
            S = 0
            X = sample_sets[ix]
            Y = sample_sets[iy]
            Z = sample_sets[iz]
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                for x in X:
                    for y in Y:
                        for z in Z:
                            xy_mrca = tr.mrca(x, y)
                            xz_mrca = tr.mrca(x, z)
                            yz_mrca = tr.mrca(y, z)
                            if xy_mrca == xz_mrca:
                                #   /\
                                #  / /\
                                # x y  z
                                S += path_length(tr, x, yz_mrca) * this_length
                            elif xy_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # y x  z
                                S += path_length(tr, x, xz_mrca) * this_length
                            elif xz_mrca == yz_mrca:
                                #   /\
                                #  / /\
                                # z x  y
                                S += path_length(tr, x, xy_mrca) * this_length
            denom = len(X) * len(Y) * len(Z)
            if span_normalise:
                denom *= (end - begin)
            out[j][i] = S / denom
    return out


def site_Y3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    haps = ts.genotype_matrix().T
    site_positions = ts.tables.sites.position
    samples = ts.samples()
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ix, iy, iz) in enumerate(indexes):
            X = sample_sets[ix]
            Y = sample_sets[iy]
            Z = sample_sets[iz]
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for x in X:
                        x_index = np.where(samples == x)[0][0]
                        for y in Y:
                            y_index = np.where(samples == y)[0][0]
                            for z in Z:
                                z_index = np.where(samples == z)[0][0]
                                if ((haps[x_index][k] != haps[y_index][k])
                                   and (haps[x_index][k] != haps[z_index][k])):
                                    # x|yz
                                    S += 1
            if site_in_window:
                denom = len(X) * len(Y) * len(Z)
                if span_normalise:
                    denom *= (end - begin)
                out[j][i] = S / denom
    return out


def node_Y3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (ix, iy, iz) in enumerate(indexes):
        X = sample_sets[ix]
        Y = sample_sets[iy]
        Z = sample_sets[iz]
        tX = len(X)
        tY = len(Y)
        tZ = len(Z)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2, t3 in zip(ts.trees(tracked_samples=X),
                                  ts.trees(tracked_samples=Y),
                                  ts.trees(tracked_samples=Z)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nX = t1.num_tracked_samples(u)
                    nY = t2.num_tracked_samples(u)
                    nZ = t3.num_tracked_samples(u)
                    SS[u] += nX * (tY - nY) * (tZ - nZ) + (tX - nX) * nY * nZ
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(X) * len(Y) * len(Z)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j, :, i] = S / denom
    return out


def Y3(ts, sample_sets, indexes=None, windows=None, mode="site", span_normalise=True):
    windows = ts.parse_windows(windows)
    if indexes is None:
        indexes = [(0, 1, 2)]
    method_map = {
        "site": site_Y3,
        "node": node_Y3,
        "branch": branch_Y3}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class TestY3(StatsTestCase, ThreeWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        n = np.array([len(x) for x in sample_sets])
        denom = np.array([n[i] * n[j] * n[k] for i, j, k in indexes])

        def f(x):
            numer = np.array(
                [x[i] * (n[j] - x[j]) * (n[k] - x[k]) for i, j, k in indexes])
            return numer / denom

        self.verify_definition(ts, sample_sets, indexes, windows, f, ts.Y3, Y3)


class TestBranchY3(TestY3, TopologyExamplesMixin):
    mode = "branch"


class TestNodeY3(TestY3, TopologyExamplesMixin):
    mode = "node"


class TestSiteY3(TestY3, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# f2
############################################

def branch_f2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    # this is f4(A,B;A,B) but drawing distinct samples from A and B
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ia, ib) in enumerate(indexes):
            A = sample_sets[ia]
            B = sample_sets[ib]
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                SS = 0
                for a in A:
                    for b in B:
                        for c in set(A) - {a}:
                            for d in set(B) - {b}:
                                SS += path_length(tr, tr.mrca(a, c), tr.mrca(b, d))
                                SS -= path_length(tr, tr.mrca(a, d), tr.mrca(b, c))
                S += SS * this_length
            denom = len(A) * (len(A) - 1) * len(B) * (len(B) - 1)
            if span_normalise:
                denom *= (end - begin)
            if denom == 0:
                out[j][i] = np.nan
            else:
                out[j][i] = S / denom
    return out


def site_f2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    samples = ts.samples()
    haps = ts.genotype_matrix().T
    site_positions = ts.tables.sites.position
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (iA, iB) in enumerate(indexes):
            A = sample_sets[iA]
            B = sample_sets[iB]
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for a in A:
                        a_index = np.where(samples == a)[0][0]
                        for b in B:
                            b_index = np.where(samples == b)[0][0]
                            for c in set(A) - {a}:
                                c_index = np.where(samples == c)[0][0]
                                for d in set(B) - {b}:
                                    d_index = np.where(samples == d)[0][0]
                                    if ((haps[a_index][k] == haps[c_index][k])
                                       and (haps[a_index][k] != haps[d_index][k])
                                       and (haps[a_index][k] != haps[b_index][k])):
                                        # ac|bd
                                        S += 1
                                    elif ((haps[a_index][k] == haps[d_index][k])
                                          and (haps[a_index][k] != haps[c_index][k])
                                          and (haps[a_index][k] != haps[b_index][k])):
                                        # ad|bc
                                        S -= 1
            if site_in_window:
                denom = len(A) * (len(A) - 1) * len(B) * (len(B) - 1)
                if span_normalise:
                    denom *= (end - begin)
                with suppress_division_by_zero_warning():
                    out[j][i] = S / denom
    return out


def node_f2(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (ia, ib) in enumerate(indexes):
        A = sample_sets[ia]
        B = sample_sets[ib]
        tA = len(A)
        tB = len(B)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2 in zip(ts.trees(tracked_samples=A),
                              ts.trees(tracked_samples=B)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nA = t1.num_tracked_samples(u)
                    nB = t2.num_tracked_samples(u)
                    # xy|uv - xv|uy with x,y in A, u, v in B
                    SS[u] += (nA * (nA - 1) * (tB - nB) * (tB - nB - 1)
                              + (tA - nA) * (tA - nA - 1) * nB * (nB - 1))
                    SS[u] -= 2 * nA * nB * (tA - nA) * (tB - nB)
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(A) * (len(A) - 1) * len(B) * (len(B) - 1)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j, :, i] = S / denom
    return out


def f2(ts, sample_sets, indexes=None, windows=None, mode="site", span_normalise=True):
    """
    Patterson's f2 statistic definitions.
    """
    windows = ts.parse_windows(windows)
    if indexes is None:
        indexes = [(0, 1)]
    method_map = {
        "site": site_f2,
        "node": node_f2,
        "branch": branch_f2}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class Testf2(StatsTestCase, TwoWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        n = np.array([len(x) for x in sample_sets])

        denom = np.array([n[i] * (n[i] - 1) * n[j] * (n[j] - 1) for i, j in indexes])

        def f(x):
            numer = np.array([
                x[i] * (x[i] - 1) * (n[j] - x[j]) * (n[j] - x[j] - 1)
                - x[i] * (n[i] - x[i]) * (n[j] - x[j]) * x[j]
                for i, j in indexes])
            return numer / denom

        self.verify_definition(ts, sample_sets, indexes, windows, f, ts.f2, f2)


class TestBranchf2(Testf2, TopologyExamplesMixin):
    mode = "branch"


class TestNodef2(Testf2, TopologyExamplesMixin):
    mode = "node"


class TestSitef2(Testf2, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# f3
############################################

def branch_f3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    # this is f4(A,B;A,C) but drawing distinct samples from A
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (ia, ib, ic) in enumerate(indexes):
            A = sample_sets[ia]
            B = sample_sets[ib]
            C = sample_sets[ic]
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                SS = 0
                for a in A:
                    for b in B:
                        for c in set(A) - {a}:
                            for d in C:
                                SS += path_length(tr, tr.mrca(a, c), tr.mrca(b, d))
                                SS -= path_length(tr, tr.mrca(a, d), tr.mrca(b, c))
                S += SS * this_length
            denom = len(A) * (len(A) - 1) * len(B) * len(C)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j][i] = S / denom
    return out


def site_f3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    samples = ts.samples()
    haps = ts.genotype_matrix().T
    site_positions = ts.tables.sites.position
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (iA, iB, iC) in enumerate(indexes):
            A = sample_sets[iA]
            B = sample_sets[iB]
            C = sample_sets[iC]
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for a in A:
                        a_index = np.where(samples == a)[0][0]
                        for b in B:
                            b_index = np.where(samples == b)[0][0]
                            for c in set(A) - {a}:
                                c_index = np.where(samples == c)[0][0]
                                for d in C:
                                    d_index = np.where(samples == d)[0][0]
                                    if ((haps[a_index][k] == haps[c_index][k])
                                       and (haps[a_index][k] != haps[d_index][k])
                                       and (haps[a_index][k] != haps[b_index][k])):
                                        # ac|bd
                                        S += 1
                                    elif ((haps[a_index][k] == haps[d_index][k])
                                          and (haps[a_index][k] != haps[c_index][k])
                                          and (haps[a_index][k] != haps[b_index][k])):
                                        # ad|bc
                                        S -= 1
            if site_in_window:
                denom = len(A) * (len(A) - 1) * len(B) * len(C)
                if span_normalise:
                    denom *= (end - begin)
                with suppress_division_by_zero_warning():
                    out[j][i] = S / denom
    return out


def node_f3(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (iA, iB, iC) in enumerate(indexes):
        A = sample_sets[iA]
        B = sample_sets[iB]
        C = sample_sets[iC]
        tA = len(A)
        tB = len(B)
        tC = len(C)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2, t3 in zip(ts.trees(tracked_samples=A),
                                  ts.trees(tracked_samples=B),
                                  ts.trees(tracked_samples=C)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nA = t1.num_tracked_samples(u)
                    nB = t2.num_tracked_samples(u)
                    nC = t3.num_tracked_samples(u)
                    # xy|uv - xv|uy with x,y in A, u in B and v in C
                    SS[u] += (nA * (nA - 1) * (tB - nB) * (tC - nC)
                              + (tA - nA) * (tA - nA - 1) * nB * nC)
                    SS[u] -= (nA * nC * (tA - nA) * (tB - nB)
                              + (tA - nA) * (tC - nC) * nA * nB)
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(A) * (len(A) - 1) * len(B) * len(C)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j, :, i] = S / denom
    return out


def f3(ts, sample_sets, indexes=None, windows=None, mode="site", span_normalise=True):
    """
    Patterson's f3 statistic definitions.
    """
    windows = ts.parse_windows(windows)
    if indexes is None:
        indexes = [(0, 1, 2)]
    method_map = {
        "site": site_f3,
        "node": node_f3,
        "branch": branch_f3}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class Testf3(StatsTestCase, ThreeWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        n = np.array([len(x) for x in sample_sets])
        denom = np.array([n[i] * (n[i] - 1) * n[j] * n[k] for i, j, k in indexes])

        def f(x):
            numer = np.array([
                x[i] * (x[i] - 1) * (n[j] - x[j]) * (n[k] - x[k])
                - x[i] * (n[i] - x[i]) * (n[j] - x[j]) * x[k] for i, j, k in indexes])
            return numer / denom
        self.verify_definition(ts, sample_sets, indexes, windows, f, ts.f3, f3)


class TestBranchf3(Testf3, TopologyExamplesMixin):
    mode = "branch"


class TestNodef3(Testf3, TopologyExamplesMixin):
    mode = "node"


class TestSitef3(Testf3, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# f4
############################################

def branch_f4(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (iA, iB, iC, iD) in enumerate(indexes):
            A = sample_sets[iA]
            B = sample_sets[iB]
            C = sample_sets[iC]
            D = sample_sets[iD]
            S = 0
            for tr in ts.trees():
                if tr.interval[1] <= begin:
                    continue
                if tr.interval[0] >= end:
                    break
                this_length = min(end, tr.interval[1]) - max(begin, tr.interval[0])
                SS = 0
                for a in A:
                    for b in B:
                        for c in C:
                            for d in D:
                                SS += path_length(tr, tr.mrca(a, c), tr.mrca(b, d))
                                SS -= path_length(tr, tr.mrca(a, d), tr.mrca(b, c))
                S += SS * this_length
            denom = len(A) * len(B) * len(C) * len(D)
            if span_normalise:
                denom *= (end - begin)
            out[j][i] = S / denom
    return out


def site_f4(ts, sample_sets, indexes, windows=None, span_normalise=True):
    windows = ts.parse_windows(windows)
    samples = ts.samples()
    haps = ts.genotype_matrix().T
    site_positions = ts.tables.sites.position
    out = np.zeros((len(windows) - 1, len(indexes)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for i, (iA, iB, iC, iD) in enumerate(indexes):
            A = sample_sets[iA]
            B = sample_sets[iB]
            C = sample_sets[iC]
            D = sample_sets[iD]
            S = 0
            site_in_window = False
            for k in range(ts.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    site_in_window = True
                    for a in A:
                        a_index = np.where(samples == a)[0][0]
                        for b in B:
                            b_index = np.where(samples == b)[0][0]
                            for c in C:
                                c_index = np.where(samples == c)[0][0]
                                for d in D:
                                    d_index = np.where(samples == d)[0][0]
                                    if ((haps[a_index][k] == haps[c_index][k])
                                       and (haps[a_index][k] != haps[d_index][k])
                                       and (haps[a_index][k] != haps[b_index][k])):
                                        # ac|bd
                                        S += 1
                                    elif ((haps[a_index][k] == haps[d_index][k])
                                          and (haps[a_index][k] != haps[c_index][k])
                                          and (haps[a_index][k] != haps[b_index][k])):
                                        # ad|bc
                                        S -= 1
            if site_in_window:
                denom = len(A) * len(B) * len(C) * len(D)
                if span_normalise:
                    denom *= (end - begin)
                out[j][i] = S / denom
    return out


def node_f4(ts, sample_sets, indexes, windows=None, span_normalise=True):
    out = np.zeros((len(windows) - 1, ts.num_nodes, len(indexes)))
    for i, (iA, iB, iC, iD) in enumerate(indexes):
        A = sample_sets[iA]
        B = sample_sets[iB]
        C = sample_sets[iC]
        D = sample_sets[iD]
        tA = len(A)
        tB = len(B)
        tC = len(C)
        tD = len(D)
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = np.zeros(ts.num_nodes)
            for t1, t2, t3, t4 in zip(ts.trees(tracked_samples=A),
                                      ts.trees(tracked_samples=B),
                                      ts.trees(tracked_samples=C),
                                      ts.trees(tracked_samples=D)):
                if t1.interval[1] <= begin:
                    continue
                if t1.interval[0] >= end:
                    break
                SS = np.zeros(ts.num_nodes)
                for u in t1.nodes():
                    # count number of pairwise paths going through u
                    nA = t1.num_tracked_samples(u)
                    nB = t2.num_tracked_samples(u)
                    nC = t3.num_tracked_samples(u)
                    nD = t4.num_tracked_samples(u)
                    # ac|bd - ad|bc
                    SS[u] += (nA * nC * (tB - nB) * (tD - nD)
                              + (tA - nA) * (tC - nC) * nB * nD)
                    SS[u] -= (nA * nD * (tB - nB) * (tC - nC)
                              + (tA - nA) * (tD - nD) * nB * nC)
                S += SS*(min(end, t1.interval[1]) - max(begin, t1.interval[0]))
            denom = len(A) * len(B) * len(C) * len(D)
            if span_normalise:
                denom *= (end - begin)
            with suppress_division_by_zero_warning():
                out[j, :, i] = S / denom
    return out


def f4(ts, sample_sets, indexes=None, windows=None, mode="site", span_normalise=True):
    """
    Patterson's f4 statistic definitions.
    """
    if indexes is None:
        indexes = [(0, 1, 2, 3)]
    method_map = {
        "site": site_f4,
        "node": node_f4,
        "branch": branch_f4}
    return method_map[mode](ts, sample_sets, indexes=indexes, windows=windows,
                            span_normalise=span_normalise)


class Testf4(StatsTestCase, FourWaySampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets_indexes(self, ts, sample_sets, indexes, windows):
        n = np.array([len(x) for x in sample_sets])
        denom = np.array([n[i] * n[j] * n[k] * n[l] for i, j, k, l in indexes])

        def f(x):
            numer = np.array([
                x[i] * x[k] * (n[j] - x[j]) * (n[l] - x[l])
                - x[i] * x[l] * (n[j] - x[j]) * (n[k] - x[k]) for i, j, k, l in indexes])
            return numer / denom
        self.verify_definition(ts, sample_sets, indexes, windows, f, ts.f4, f4)


class TestBranchf4(Testf4, TopologyExamplesMixin):
    mode = "branch"


class TestNodef4(Testf4, TopologyExamplesMixin):
    mode = "node"


class TestSitef4(Testf4, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# Site frequency spectrum
############################################


def naive_branch_sample_frequency_spectrum(ts, sample_sets, windows=None):
    # Draft of the 'site frequency spectrum' definition for different
    # sample sets. Take the middle dimension as the max of sizes of the
    # sample sets, and the last dimension as the different sample sets. This
    # makes it easy to drop the last dimension in the default case of all
    # samples. (But, we could definitely do it the other way around, with
    # the middle dimension being the sample set index.
    #
    # The other difference with older versions is that we're outputting
    # sfs[j] as the total branch length over j members of the set, including
    # sfs[0] for zero members. Other versions were using sfs[j - 1] for
    # total branch_length over j, and not tracking the branch length over
    # 0. The current approach seems more natura to me.

    windows = ts.parse_windows(windows)
    n_out = 1 + max(len(sample_set) for sample_set in sample_sets)
    out = np.zeros((len(windows) - 1, n_out, len(sample_sets)))
    for j in range(len(windows) - 1):
        begin = windows[j]
        end = windows[j + 1]
        for set_index, sample_set in enumerate(sample_sets):
            S = np.zeros((n_out))
            for t in ts.trees(tracked_samples=sample_set, sample_counts=True):
                tr_len = min(end, t.interval[1]) - max(begin, t.interval[0])
                if tr_len > 0:
                    for node in t.nodes():
                        x = t.num_tracked_samples(node)
                        S[x] += t.branch_length(node) * tr_len
            out[j, :, set_index] = S / (end - begin)
    return out


def naive_sample_frequency_spectrum(ts, sample_sets, windows=None, mode="site"):
    """
    Naive definition of the generalised site frequency spectrum.
    """
    method_map = {
        # "site": naive_site_sample_frequency_spectrum,
        "branch": naive_branch_sample_frequency_spectrum}
    return method_map[mode](ts, sample_sets, windows=windows)


def branch_sample_frequency_spectrum(ts, sample_sets, windows):
    """
    Efficient implementation of the algorithm used as the basis for the
    underlying C version.
    """
    num_sample_sets = len(sample_sets)
    n_out = 1 + max(len(sample_set) for sample_set in sample_sets)
    windows = ts.parse_windows(windows)
    num_windows = windows.shape[0] - 1

    result = np.zeros((num_windows, n_out, num_sample_sets))
    state = np.zeros((ts.num_nodes, num_sample_sets), dtype=np.uint32)
    for j in range(num_sample_sets):
        state[sample_sets[j], j] = 1

    def area_weighted_summary(u):
        v = parent[u]
        branch_length = 0
        s = np.zeros((n_out, num_sample_sets))
        if v != -1:
            branch_length = time[v] - time[u]
        if branch_length > 0:
            count = state[u]
            for j in range(num_sample_sets):
                s[count[j], j] += branch_length
        return s

    tree_index = 0
    window_index = 0
    time = ts.tables.nodes.time
    parent = np.zeros(ts.num_nodes, dtype=np.int32) - 1
    running_sum = np.zeros((n_out, num_sample_sets))
    for (t_left, t_right), edges_out, edges_in in ts.edge_diffs():
        for edge in edges_out:
            u = edge.child
            running_sum -= area_weighted_summary(u)
            u = edge.parent
            while u != -1:
                running_sum -= area_weighted_summary(u)
                state[u] -= state[edge.child]
                running_sum += area_weighted_summary(u)
                u = parent[u]
            parent[edge.child] = -1

        for edge in edges_in:
            parent[edge.child] = edge.parent
            u = edge.child
            running_sum += area_weighted_summary(u)
            u = edge.parent
            while u != -1:
                running_sum -= area_weighted_summary(u)
                state[u] += state[edge.child]
                running_sum += area_weighted_summary(u)
                u = parent[u]

        # Update the windows
        assert window_index < num_windows
        while windows[window_index] < t_right:
            w_left = windows[window_index]
            w_right = windows[window_index + 1]
            left = max(t_left, w_left)
            right = min(t_right, w_right)
            weight = right - left
            assert weight > 0
            result[window_index] += running_sum * weight
            if w_right <= t_right:
                window_index += 1
            else:
                # This interval crosses a tree boundary, so we update it again in the
                # for the next tree
                break

        tree_index += 1

    # print("window_index:", window_index, windows.shape)
    assert window_index == windows.shape[0] - 1
    for j in range(num_windows):
        result[j] /= windows[j + 1] - windows[j]
    return result


def sample_frequency_spectrum(ts, sample_sets, windows=None, mode="site"):
    """
    Generalised site frequency spectrum.
    """
    method_map = {
        # "site": site_sample_frequency_spectrum,
        "branch": branch_sample_frequency_spectrum}
    return method_map[mode](ts, sample_sets, windows=windows)


class TestSampleFrequencySpectrum(StatsTestCase, SampleSetStatsMixin):

    # Derived classes define this to get a specific stats mode.
    mode = None

    def verify_sample_sets(self, ts, sample_sets, windows):
        # print("Verify", sample_sets, windows)
        sfs1 = naive_sample_frequency_spectrum(ts, sample_sets, windows, mode=self.mode)
        sfs2 = sample_frequency_spectrum(ts, sample_sets, windows, mode=self.mode)
        self.assertEqual(sfs1.shape[0], len(windows) - 1)
        self.assertEqual(sfs1.shape, sfs2.shape)
        # print(sfs1)
        # print(sfs2)
        self.assertArrayAlmostEqual(sfs1, sfs2)
        # print(sfs2.shape)


class TestBranchSampleFrequencySpectrum(
        TestSampleFrequencySpectrum, TopologyExamplesMixin):
    mode = "branch"

    def test_simple_example(self):
        ts = msprime.simulate(6, random_seed=1)
        self.verify_sample_sets(ts, [[0, 1, 2], [3, 4, 5]], [0, 1])

    @unittest.skip("Mismatch when multiple roots")
    def test_wright_fisher_simplified_multiple_roots(self):
        pass

    @unittest.skip("Mismatch when multiple roots")
    def test_wright_fisher_unsimplified_multiple_roots(self):
        pass


@unittest.skip("Not working yet")
class TestSiteSampleFrequencySpectrum(
        TestSampleFrequencySpectrum, MutatedTopologyExamplesMixin):
    mode = "site"


############################################
# End of specific stats tests.
############################################


class TestWindowedTreeStat(StatsTestCase):
    """
    Tests that the treewise windowing function defined here has the correct
    behaviour.
    """
    # TODO add more tests here covering the various windowing possibilities.
    def get_tree_sequence(self):
        ts = msprime.simulate(10, recombination_rate=2, random_seed=1)
        self.assertGreater(ts.num_trees, 3)
        return ts

    def test_all_trees(self):
        ts = self.get_tree_sequence()
        A1 = np.ones((ts.num_trees, 1))
        windows = np.array(list(ts.breakpoints()))
        A2 = windowed_tree_stat(ts, A1, windows)
        # print("breakpoints = ", windows)
        # print(A2)
        self.assertEqual(A1.shape, A2.shape)
        # JK: I don't understand what we're computing here, this normalisation
        # seems pretty weird.
        # for tree in ts.trees():
        #     self.assertAlmostEqual(A2[tree.index, 0], tree.span / ts.sequence_length)

    def test_single_interval(self):
        ts = self.get_tree_sequence()
        A1 = np.ones((ts.num_trees, 1))
        windows = np.array([0, ts.sequence_length])
        A2 = windowed_tree_stat(ts, A1, windows)
        self.assertEqual(A2.shape, (1, 1))
        # TODO: Test output


class TestSampleSets(StatsTestCase):
    """
    Tests that passing sample sets in various ways gets interpreted correctly.
    """

    def test_duplicate_samples(self):
        ts = msprime.simulate(10, mutation_rate=1, random_seed=2)
        for bad_set in [[1, 1], [1, 2, 1], list(range(10)) + [9]]:
            with self.assertRaises(exceptions.LibraryError):
                ts.diversity([bad_set])
            with self.assertRaises(exceptions.LibraryError):
                ts.divergence([[0, 1], bad_set])
            with self.assertRaises(ValueError):
                ts.sample_count_stat([bad_set], lambda x: x)

    def test_empty_sample_set(self):
        ts = msprime.simulate(10, mutation_rate=1, random_seed=2)
        with self.assertRaises(ValueError):
            ts.diversity([[]])
        for bad_sample_sets in [[[], []], [[1], []], [[1, 2], [1], []]]:
            with self.assertRaises(ValueError):
                ts.diversity(bad_sample_sets)
            with self.assertRaises(ValueError):
                ts.divergence(bad_sample_sets)
            with self.assertRaises(ValueError):
                ts.sample_count_stat(bad_sample_sets, lambda x: x)

    def test_non_samples(self):
        ts = msprime.simulate(10, mutation_rate=1, random_seed=2)
        with self.assertRaises(exceptions.LibraryError):
            ts.diversity([[10]])

        with self.assertRaises(exceptions.LibraryError):
            ts.divergence([[10], [1, 2]])

        with self.assertRaises(ValueError):
            ts.sample_count_stat([[10]], lambda x: x)


class TestSampleSetIndexes(StatsTestCase):
    """
    Tests that we get the correct behaviour from the indexes argument to
    k-way stats functions.
    """
    def get_example_ts(self):
        ts = msprime.simulate(10, mutation_rate=1, random_seed=1)
        self.assertGreater(ts.num_mutations, 0)
        return ts

    def test_2_way_default(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 2)
        S1 = ts.divergence(sample_sets)
        S2 = divergence(ts, sample_sets)
        S3 = ts.divergence(sample_sets, [[0, 1]])
        self.assertEqual(S1.shape, S2.shape)
        self.assertArrayAlmostEqual(S1, S2)
        self.assertArrayAlmostEqual(S1, S3)

    def test_3_way_default(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 3)
        S1 = ts.f3(sample_sets)
        S2 = f3(ts, sample_sets)
        S3 = ts.f3(sample_sets, [[0, 1, 2]])
        self.assertEqual(S1.shape, S2.shape)
        self.assertArrayAlmostEqual(S1, S2)
        self.assertArrayAlmostEqual(S1, S3)

    def test_4_way_default(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 4)
        S1 = ts.f4(sample_sets)
        S2 = f4(ts, sample_sets)
        S3 = ts.f4(sample_sets, [[0, 1, 2, 3]])
        self.assertEqual(S1.shape, S2.shape)
        self.assertArrayAlmostEqual(S1, S2)
        self.assertArrayAlmostEqual(S1, S3)

    def test_2_way_combinations(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 4)
        pairs = list(itertools.combinations(range(4), 2))
        for k in range(1, len(pairs)):
            S1 = ts.divergence(sample_sets, pairs[:k])
            S2 = divergence(ts, sample_sets, pairs[:k])
            self.assertEqual(S1.shape[-1], k)
            self.assertEqual(S1.shape, S2.shape)
            self.assertArrayAlmostEqual(S1, S2)

    def test_3_way_combinations(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 5)
        triples = list(itertools.combinations(range(5), 3))
        for k in range(1, len(triples)):
            S1 = ts.Y3(sample_sets, triples[:k])
            S2 = Y3(ts, sample_sets, triples[:k])
            self.assertEqual(S1.shape[-1], k)
            self.assertEqual(S1.shape, S2.shape)
            self.assertArrayAlmostEqual(S1, S2)

    def test_4_way_combinations(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 5)
        quads = list(itertools.combinations(range(5), 4))
        for k in range(1, len(quads)):
            S1 = ts.f4(sample_sets, quads[:k])
            S2 = f4(ts, sample_sets, quads[:k])
            self.assertEqual(S1.shape[-1], k)
            self.assertEqual(S1.shape, S2.shape)
            self.assertArrayAlmostEqual(S1, S2)

    def test_errors(self):
        ts = self.get_example_ts()
        sample_sets = np.array_split(ts.samples(), 2)
        with self.assertRaises(ValueError):
            ts.divergence(sample_sets, indexes=[])
        with self.assertRaises(ValueError):
            ts.divergence(sample_sets, indexes=[(1, 1, 1)])
        with self.assertRaises(exceptions.LibraryError):
            ts.divergence(sample_sets, indexes=[(1, 2)])


class TestGeneralStatInterface(StatsTestCase):
    """
    Tests for the basic interface for general_stats.
    """

    def get_tree_sequence(self):
        ts = msprime.simulate(10, recombination_rate=2,
                              mutation_rate=2, random_seed=1)
        return ts

    def test_default_mode(self):
        ts = msprime.simulate(10, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 2))
        sigma1 = ts.general_stat(W, lambda x: x)
        sigma2 = ts.general_stat(W, lambda x: x, mode="site")
        self.assertArrayEqual(sigma1, sigma2)

    def test_bad_mode(self):
        ts = msprime.simulate(10, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 2))
        for bad_mode in ["", "MODE", "x" * 8192]:
            with self.assertRaises(ValueError):
                ts.general_stat(W, lambda x: x, mode=bad_mode)

    def test_bad_window_strings(self):
        ts = self.get_tree_sequence()
        with self.assertRaises(ValueError):
            ts.diversity([list(ts.samples())], mode="site", windows="abc")
        with self.assertRaises(ValueError):
            ts.diversity([list(ts.samples())], mode="site", windows="")
        with self.assertRaises(ValueError):
            ts.diversity([list(ts.samples())], mode="tree", windows="abc")


class TestGeneralBranchStats(StatsTestCase):
    """
    Tests for general branch stats (using functions and arbitrary weights)
    """
    def compare_general_stat(self, ts, W, f, windows=None, polarised=False):
        sigma1 = naive_branch_general_stat(ts, W, f, windows, polarised=polarised)
        sigma2 = ts.general_stat(W, f, windows, polarised=polarised, mode="branch")
        sigma3 = branch_general_stat(ts, W, f, windows, polarised=polarised)
        self.assertEqual(sigma1.shape, sigma2.shape)
        self.assertEqual(sigma1.shape, sigma3.shape)
        self.assertArrayAlmostEqual(sigma1, sigma2)
        self.assertArrayAlmostEqual(sigma1, sigma3)
        return sigma1

    def test_simple_identity_f_w_zeros(self):
        ts = msprime.simulate(12, recombination_rate=3, random_seed=2)
        W = np.zeros((ts.num_samples, 3))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(ts, W, lambda x: x, windows="trees",
                                              polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_trees, W.shape[1]))
            self.assertTrue(np.all(sigma == 0))

    def test_simple_identity_f_w_ones(self):
        ts = msprime.simulate(10, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 2))
        sigma = self.compare_general_stat(ts, W, lambda x: x, windows="trees",
                                          polarised=True)
        self.assertEqual(sigma.shape, (ts.num_trees, W.shape[1]))
        # A W of 1 for every node and identity f counts the samples in the subtree
        # if polarised is True.
        for tree in ts.trees():
            s = sum(tree.num_samples(u) * tree.branch_length(u) for u in tree.nodes())
            self.assertTrue(np.allclose(sigma[tree.index], s))

    def test_simple_cumsum_f_w_ones(self):
        ts = msprime.simulate(13, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 8))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(
                ts, W, lambda x: np.cumsum(x), windows="trees", polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_trees, W.shape[1]))

    def test_simple_cumsum_f_w_ones_many_windows(self):
        ts = msprime.simulate(15, recombination_rate=3, random_seed=3)
        self.assertGreater(ts.num_trees, 3)
        windows = np.linspace(0, ts.sequence_length, num=ts.num_trees * 10)
        W = np.ones((ts.num_samples, 3))
        sigma = self.compare_general_stat(ts, W, lambda x: np.cumsum(x), windows=windows)
        self.assertEqual(sigma.shape, (windows.shape[0] - 1, W.shape[1]))

    def test_windows_equal_to_ts_breakpoints(self):
        ts = msprime.simulate(14, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 1))
        for polarised in [True, False]:
            sigma_no_windows = self.compare_general_stat(
                ts, W, lambda x: np.cumsum(x), windows="trees", polarised=polarised)
            self.assertEqual(sigma_no_windows.shape, (ts.num_trees, W.shape[1]))
            sigma_windows = self.compare_general_stat(
                ts, W, lambda x: np.cumsum(x), windows=ts.breakpoints(as_array=True),
                polarised=polarised)
            self.assertEqual(sigma_windows.shape, sigma_no_windows.shape)
            self.assertTrue(np.allclose(sigma_windows.shape, sigma_no_windows.shape))

    def test_single_tree_windows(self):
        ts = msprime.simulate(15, random_seed=2, length=100)
        W = np.ones((ts.num_samples, 2))
        # for num_windows in range(1, 10):
        for num_windows in [2]:
            windows = np.linspace(0, ts.sequence_length, num=num_windows + 1)
            sigma = self.compare_general_stat(ts, W, lambda x: np.array([np.sum(x)]),
                                              windows)
            self.assertEqual(sigma.shape, (num_windows, 1))

    def test_simple_identity_f_w_zeros_windows(self):
        ts = msprime.simulate(15, recombination_rate=3, random_seed=2)
        W = np.zeros((ts.num_samples, 3))
        windows = np.linspace(0, ts.sequence_length, num=11)
        for polarised in [True, False]:
            sigma = self.compare_general_stat(ts, W, lambda x: x, windows,
                                              polarised=polarised)
            self.assertEqual(sigma.shape, (10, W.shape[1]))
            self.assertTrue(np.all(sigma == 0))


class TestGeneralSiteStats(StatsTestCase):
    """
    Tests for general site stats (using functions and arbitrary weights)
    """
    def compare_general_stat(self, ts, W, f, windows=None, polarised=False):
        py_ssc = PythonSiteStatCalculator(ts)
        sigma1 = py_ssc.naive_general_stat(W, f, windows, polarised=polarised)
        sigma2 = ts.general_stat(W, f, windows, polarised=polarised, mode="site")
        sigma3 = site_general_stat(ts, W, f, windows, polarised=polarised)
        self.assertEqual(sigma1.shape, sigma2.shape)
        self.assertEqual(sigma1.shape, sigma3.shape)
        self.assertArrayAlmostEqual(sigma1, sigma2)
        self.assertArrayAlmostEqual(sigma1, sigma3)
        return sigma1

    def test_identity_f_W_0_multiple_alleles(self):
        ts = msprime.simulate(20, recombination_rate=0, random_seed=2)
        ts = tsutil.jukes_cantor(ts, 20, 1, seed=10)
        W = np.zeros((ts.num_samples, 3))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(ts, W, lambda x: x, windows="sites",
                                              polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_sites, W.shape[1]))
            self.assertTrue(np.all(sigma == 0))

    def test_identity_f_W_0_multiple_alleles_windows(self):
        ts = msprime.simulate(34, recombination_rate=0, random_seed=2)
        ts = tsutil.jukes_cantor(ts, 20, 1, seed=10)
        W = np.zeros((ts.num_samples, 3))
        windows = np.linspace(0, 1, num=11)
        for polarised in [True, False]:
            sigma = self.compare_general_stat(
                ts, W, lambda x: x, windows=windows, polarised=polarised)
            self.assertEqual(sigma.shape, (windows.shape[0] - 1, W.shape[1]))
            self.assertTrue(np.all(sigma == 0))

    def test_cumsum_f_W_1_multiple_alleles(self):
        ts = msprime.simulate(3, recombination_rate=2, random_seed=2)
        ts = tsutil.jukes_cantor(ts, 20, 1, seed=10)
        W = np.ones((ts.num_samples, 3))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(ts, W, lambda x: np.cumsum(x),
                                              windows="sites", polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_sites, W.shape[1]))

    def test_cumsum_f_W_1_two_alleles(self):
        ts = msprime.simulate(33, recombination_rate=1, mutation_rate=2, random_seed=1)
        W = np.ones((ts.num_samples, 5))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(
                ts, W, lambda x: np.cumsum(x), windows="sites", polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_sites, W.shape[1]))


class TestGeneralNodeStats(StatsTestCase):
    """
    Tests for general node stats (using functions and arbitrary weights)
    """
    def compare_general_stat(self, ts, W, f, windows=None, polarised=False):
        sigma1 = naive_node_general_stat(ts, W, f, windows, polarised=polarised)
        sigma2 = ts.general_stat(W, f, windows, polarised=polarised, mode="node")
        sigma3 = node_general_stat(ts, W, f, windows, polarised=polarised)
        self.assertEqual(sigma1.shape, sigma2.shape)
        self.assertEqual(sigma1.shape, sigma3.shape)
        self.assertArrayAlmostEqual(sigma1, sigma2)
        self.assertArrayAlmostEqual(sigma1, sigma3)
        return sigma1

    def test_simple_sum_f_w_zeros(self):
        ts = msprime.simulate(12, recombination_rate=3, random_seed=2)
        W = np.zeros((ts.num_samples, 3))
        for polarised in [True, False]:
            sigma = self.compare_general_stat(
                ts, W, lambda x: x, windows="trees", polarised=polarised)
            self.assertEqual(sigma.shape, (ts.num_trees, ts.num_nodes, 3))
            self.assertTrue(np.all(sigma == 0))

    def test_simple_sum_f_w_ones(self):
        ts = msprime.simulate(44, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 2))
        sigma = self.compare_general_stat(
            ts, W, lambda x: np.array([sum(x)]), windows="trees", polarised=True)
        self.assertEqual(sigma.shape, (ts.num_trees, ts.num_nodes, 1))
        # Drop the last dimension
        sigma = sigma.reshape((ts.num_trees, ts.num_nodes))
        # A W of 1 for every node and f(x)=sum(x) counts the samples in the subtree
        # times 2 if polarised is True.
        for tree in ts.trees():
            s = np.array([tree.num_samples(u) for u in range(ts.num_nodes)])
            self.assertArrayAlmostEqual(sigma[tree.index], 2*s)

    def test_small_tree_windows_polarised(self):
        ts = msprime.simulate(4, recombination_rate=0.5, random_seed=2)
        self.assertGreater(ts.num_trees, 1)
        W = np.ones((ts.num_samples, 1))
        sigma = self.compare_general_stat(
            ts, W, lambda x: np.cumsum(x), windows=ts.breakpoints(as_array=True),
            polarised=True)
        self.assertEqual(sigma.shape, (ts.num_trees, ts.num_nodes, 1))

    def test_one_window_polarised(self):
        ts = msprime.simulate(4, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 1))
        sigma = self.compare_general_stat(
            ts, W, lambda x: np.cumsum(x), windows=[0, ts.sequence_length],
            polarised=True)
        self.assertEqual(sigma.shape, (1, ts.num_nodes, W.shape[1]))

    @unittest.skip("Funny things happening for unpolarised")
    def test_one_window_unpolarised(self):
        ts = msprime.simulate(4, recombination_rate=1, random_seed=2)
        W = np.ones((ts.num_samples, 2))
        sigma = self.compare_general_stat(
            ts, W, lambda x: np.cumsum(x), windows=[0, ts.sequence_length],
            polarised=False)
        self.assertEqual(sigma.shape, (1, ts.num_nodes, 2))

    def test_many_windows(self):
        ts = msprime.simulate(24, recombination_rate=3, random_seed=2)
        W = np.ones((ts.num_samples, 3))
        for k in [1, ts.num_trees // 2, ts.num_trees, ts.num_trees * 2]:
            windows = np.linspace(0, 1, num=k + 1)
            for polarised in [True]:
                sigma = self.compare_general_stat(
                    ts, W, lambda x: np.cumsum(x), windows=windows, polarised=polarised)
            self.assertEqual(sigma.shape, (k, ts.num_nodes, 3))

    def test_one_tree(self):
        ts = msprime.simulate(10, random_seed=3)
        W = np.ones((ts.num_samples, 2))
        sigma = self.compare_general_stat(
            ts, W, lambda x: np.array([sum(x), sum(x)]), windows=[0, 1], polarised=True)
        self.assertEqual(sigma.shape, (1, ts.num_nodes, 2))
        # A W of 1 for every node and f(x)=sum(x) counts the samples in the subtree
        # times 2 if polarised is True.
        tree = ts.first()
        s = np.array([tree.num_samples(u) for u in range(ts.num_nodes)])
        self.assertArrayAlmostEqual(sigma[tree.index, :, 0], 2 * s)
        self.assertArrayAlmostEqual(sigma[tree.index, :, 1], 2 * s)


@unittest.skip("Broken - need to port tests")
class SampleSetStatTestCase(StatsTestCase):
    """
    Provides checks for testing of sample set-based statistics.  Actual testing
    is done by derived classes, which should have attributes `stat_type` and `rng`.
    This works by using parallel structure between different statistic "modes",
    in tree sequence methods (with stat_type=X) and python stat calculators as
    implemented here (with StatCalculator.X).
    """

    random_seed = 123456

    def compare_sfs(self, ts, tree_fn, sample_sets, tsc_fn):
        for sample_set in sample_sets:
            windows = [k * ts.sequence_length / 20 for k in
                       [0] + sorted(self.rng.sample(range(1, 20), 4)) + [20]]
            win_args = [{'begin': windows[i], 'end': windows[i+1]}
                        for i in range(len(windows)-1)]
            tree_vals = [tree_fn(sample_set, **b) for b in win_args]

            tsc_vals = tsc_fn(sample_set, windows)
            self.assertEqual(len(tsc_vals), len(windows) - 1)
            for i in range(len(windows) - 1):
                self.assertListAlmostEqual(tsc_vals[i], tree_vals[i])

    def check_sfs_interface(self, ts):
        samples = ts.samples()

        # empty sample sets will raise an error
        self.assertRaises(ValueError, ts.site_frequency_spectrum, [],
                          self.stat_type)
        # sample_sets must be lists without repeated elements
        self.assertRaises(ValueError, ts.site_frequency_spectrum,
                          [samples[2], samples[2]], self.stat_type)
        # and must all be samples
        self.assertRaises(ValueError, ts.site_frequency_spectrum,
                          [samples[0], max(samples)+1], self.stat_type)
        # windows must start at 0.0, be increasing, and extend to the end
        self.assertRaises(ValueError, ts.site_frequency_spectrum,
                          samples[0:2], [0.1, ts.sequence_length],
                          self.stat_type)
        self.assertRaises(ValueError, ts.site_frequency_spectrum,
                          samples[0:2], [0.0, 0.8*ts.sequence_length],
                          self.stat_type)
        self.assertRaises(ValueError, ts.site_frequency_spectrum, samples[0:2],
                          [0.0, 0.8*ts.sequence_length, 0.4*ts.sequence_length,
                           ts.sequence_length], self.stat_type)

    def check_sfs(self, ts):
        # check site frequency spectrum
        self.check_sfs_interface(ts)
        A = [self.rng.sample(list(ts.samples()), 2),
             self.rng.sample(list(ts.samples()), 4),
             self.rng.sample(list(ts.samples()), 8),
             self.rng.sample(list(ts.samples()), 10),
             self.rng.sample(list(ts.samples()), 12)]
        py_tsc = self.py_stat_class(ts)

        self.compare_sfs(ts, py_tsc.site_frequency_spectrum, A,
                         ts.site_frequency_spectrum)


class BranchSampleSetStatsTestCase(SampleSetStatTestCase):
    """
    Tests of branch statistic computation with sample sets,
    mostly running the checks in SampleSetStatTestCase.
    """

    def setUp(self):
        self.rng = random.Random(self.random_seed)
        self.stat_type = "branch"
        self.py_stat_class = PythonBranchStatCalculator

    def get_ts(self):
        for N in [12, 15, 20]:
            yield msprime.simulate(N, random_seed=self.random_seed,
                                   recombination_rate=10)

    @unittest.skip("Skipping SFS.")
    def test_sfs_interface(self):
        ts = msprime.simulate(10)
        tsc = tskit.BranchStatCalculator(ts)

        # Duplicated samples raise an error
        self.assertRaises(ValueError, tsc.site_frequency_spectrum, [1, 1])
        self.assertRaises(ValueError, tsc.site_frequency_spectrum, [])
        self.assertRaises(ValueError, tsc.site_frequency_spectrum, [0, 11])
        # Check for bad windows
        for bad_start in [-1, 1, 1e-7]:
            self.assertRaises(
                ValueError, tsc.site_frequency_spectrum, [1, 2],
                [bad_start, ts.sequence_length])
        for bad_end in [0, ts.sequence_length - 1, ts.sequence_length + 1]:
            self.assertRaises(
                ValueError, tsc.site_frequency_spectrum, [1, 2],
                [0, bad_end])
        # Windows must be increasing.
        self.assertRaises(
            ValueError, tsc.site_frequency_spectrum, [1, 2], [0, 1, 1])

    @unittest.skip("No SFS.")
    def test_branch_sfs(self):
        for ts in self.get_ts():
            self.check_sfs(ts)


class SpecificTreesTestCase(StatsTestCase):
    """
    Some particular cases, that are easy to see and debug.
    """
    seed = 21

    def test_case_1(self):
        # With mutations:
        #
        # 1.0          6
        # 0.7         / \                                    5
        #            /   X                                  / \
        # 0.5       X     4                4               /   4
        #          /     / \              / \             /   X X
        # 0.4     X     X   \            X   3           X   /   \
        #        /     /     X          /   / X         /   /     \
        # 0.0   0     1       2        1   0   2       0   1       2
        #          (0.0, 0.2),        (0.2, 0.8),       (0.8, 1.0)
        #
        branch_true_diversity_01 = 2*(1 * (0.2-0) +
                                      0.5 * (0.8-0.2) + 0.7 * (1.0-0.8))
        branch_true_diversity_02 = 2*(1 * (0.2-0) +
                                      0.4 * (0.8-0.2) + 0.7 * (1.0-0.8))
        branch_true_diversity_12 = 2*(0.5 * (0.2-0) +
                                      0.5 * (0.8-0.2) + 0.5 * (1.0-0.8))
        branch_true_Y = 0.2*(1 + 0.5) + 0.6*(0.4) + 0.2*(0.7+0.2)
        site_true_Y = 3 + 0 + 1
        node_true_diversity_012 = np.array([
                0.2 * np.array([2, 2, 2, 0, 2, 0, 0]) +
                0.6 * np.array([2, 2, 2, 2, 0, 0, 0]) +
                0.2 * np.array([2, 2, 2, 0, 2, 0, 0])]) / 3
        node_true_divergence_0_12 = np.array([
                0.2 * np.array([2, 1, 1, 0, 2, 0, 0]) +
                0.6 * np.array([2, 1, 1, 1, 0, 0, 0]) +
                0.2 * np.array([2, 1, 1, 0, 2, 0, 0])]) / 2

        nodes = io.StringIO("""\
        id      is_sample   time
        0       1           0
        1       1           0
        2       1           0
        3       0           0.4
        4       0           0.5
        5       0           0.7
        6       0           1.0
        """)
        edges = io.StringIO("""\
        left    right   parent  child
        0.2     0.8     3       0,2
        0.0     0.2     4       1,2
        0.2     0.8     4       1,3
        0.8     1.0     4       1,2
        0.8     1.0     5       0,4
        0.0     0.2     6       0,4
        """)
        sites = io.StringIO("""\
        id  position    ancestral_state
        0   0.05        0
        1   0.1         0
        2   0.11        0
        3   0.15        0
        4   0.151       0
        5   0.3         0
        6   0.6         0
        7   0.9         0
        8   0.95        0
        9   0.951       0
        """)
        mutations = io.StringIO("""\
        site    node    derived_state
        0       4       1
        1       0       1
        2       2       1
        3       0       1
        4       1       1
        5       1       1
        6       2       1
        7       0       1
        8       1       1
        9       2       1
        """)
        ts = tskit.load_text(
            nodes=nodes, edges=edges, sites=sites, mutations=mutations,
            strict=False)

        # diversity between 0 and 1
        A = [[0], [1]]
        n = [len(a) for a in A]

        def f(x):
            return np.array([float(x[0]*(n[1]-x[1]) + (n[0]-x[0])*x[1])/(2*n[0]*n[1])])

        # tree lengths:
        mode = "branch"
        self.assertAlmostEqual(divergence(ts, [[0], [1]], [(0, 1)], mode=mode),
                               branch_true_diversity_01)
        self.assertAlmostEqual(ts.divergence([[0], [1]], [(0, 1)], mode=mode),
                               branch_true_diversity_01)
        self.assertAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0],
                               branch_true_diversity_01)
        self.assertAlmostEqual(ts.diversity([[0, 1]], mode=mode)[0][0],
                               branch_true_diversity_01)

        # mean diversity between [0, 1] and [0, 2]:
        branch_true_mean_diversity = (0 + branch_true_diversity_02
                                      + branch_true_diversity_01
                                      + branch_true_diversity_12)/4
        A = [[0, 1], [0, 2]]
        n = [len(a) for a in A]

        def f(x):
            return np.array([float(x[0]*(n[1]-x[1]) + (n[0]-x[0])*x[1])/8.0])

        # tree lengths:
        self.assertAlmostEqual(divergence(ts, [A[0], A[1]], [(0, 1)], mode=mode),
                               branch_true_mean_diversity)
        self.assertAlmostEqual(ts.divergence([A[0], A[1]], [(0, 1)], mode=mode),
                               branch_true_mean_diversity)
        self.assertAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0],
                               branch_true_mean_diversity)

        # Y-statistic for (0/12)
        A = [[0], [1, 2]]

        def f(x):
            return np.array([float(((x[0] == 1) and (x[1] == 0))
                                   or ((x[0] == 0) and (x[1] == 2)))/2.0])

        # tree lengths:
        bts_Y = ts.Y3([[0], [1], [2]], windows=[0.0, 1.0], mode=mode)[0][0]
        py_bsc_Y = Y3(ts, [[0], [1], [2]], [(0, 1, 2)], windows=[0.0, 1.0], mode=mode)
        self.assertArrayAlmostEqual(bts_Y, branch_true_Y)
        self.assertArrayAlmostEqual(py_bsc_Y, branch_true_Y)
        self.assertArrayAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0],
                                    branch_true_Y)

        mode = "site"
        # sites, Y:
        sts_Y = ts.Y3([[0], [1], [2]], windows=[0.0, 1.0], mode=mode)[0][0]
        py_ssc_Y = Y3(ts, [[0], [1], [2]], [(0, 1, 2)], windows=[0.0, 1.0], mode=mode)
        self.assertArrayAlmostEqual(sts_Y, site_true_Y)
        self.assertArrayAlmostEqual(py_ssc_Y, site_true_Y)
        self.assertArrayAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0],
                                    site_true_Y)

        A = [[0, 1, 2]]
        n = 3
        W = np.array([[u in A[0]] for u in ts.samples()], dtype=float)

        def f(x):
            return np.array([x[0]*(n-x[0])/(n * (n - 1))])

        mode = "node"
        # nodes, diversity in [0,1,2]
        nodes_div_012 = ts.diversity([[0, 1, 2]], mode=mode).reshape((1, 7))
        py_nodes_div_012 = diversity(ts, [[0, 1, 2]], mode=mode).reshape((1, 7))
        py_general_nodes_div_012 = general_stat(ts, W, f, mode=mode).reshape((1, 7))
        self.assertArrayAlmostEqual(py_nodes_div_012, node_true_diversity_012)
        self.assertArrayAlmostEqual(py_general_nodes_div_012, node_true_diversity_012)
        self.assertArrayAlmostEqual(nodes_div_012, node_true_diversity_012)

        # nodes, divergence [0] to [1,2]
        nodes_div_0_12 = ts.divergence([[0], [1, 2]], mode=mode).reshape((1, 7))
        py_nodes_div_0_12 = divergence(ts, [[0], [1, 2]], mode=mode).reshape((1, 7))
        self.assertArrayAlmostEqual(nodes_div_0_12, node_true_divergence_0_12)
        self.assertArrayAlmostEqual(py_nodes_div_0_12, node_true_divergence_0_12)

    def test_case_odds_and_ends(self):
        # Tests having (a) the first site after the first window, and
        # (b) no samples having the ancestral state.
        nodes = io.StringIO("""\
        id      is_sample   time
        0       1           0
        1       1           0
        2       0           0.5
        3       0           1.0
        """)
        edges = io.StringIO("""\
        left    right   parent  child
        0.0     0.5     2       0,1
        0.5     1.0     3       0,1
        """)
        sites = io.StringIO("""\
        id  position    ancestral_state
        0   0.65        0
        """)
        mutations = io.StringIO("""\
        site    node    derived_state   parent
        0       0       1               -1
        0       1       2               -1
        """)
        ts = tskit.load_text(
            nodes=nodes, edges=edges, sites=sites, mutations=mutations,
            strict=False)

        mode = "site"
        py_div = divergence(
            ts, [[0], [1]], indexes=[(0, 1)], windows=[0.0, 0.5, 1.0], mode=mode)
        div = ts.divergence(
            [[0], [1]], indexes=[(0, 1)], windows=[0.0, 0.5, 1.0], mode=mode)
        self.assertArrayEqual(py_div, div)

    def test_case_four_taxa(self):
        #
        # 1.0          7
        # 0.7         / \                                    6
        #            /   \                                  / \
        # 0.5       /     5              5                 /   5
        #          /     / \            / \__             /   / \
        # 0.4     /     8   \          8     4           /   8   \
        #        /     / \   \        / \   / \         /   / \   \
        # 0.0   0     1   3   2      1   3 0   2       0   1   3   2
        #          (0.0, 0.2),        (0.2, 0.8),       (0.8, 2.5)

        # f4(0, 1, 2, 3): (0 -> 1)(2 -> 3)
        branch_true_f4_0123 = (0.1 * 0.2 + (0.1 + 0.1) * 0.6 + 0.1 * 1.7) / 2.5
        windows = [0.0, 0.4, 2.5]
        branch_true_f4_0123_windowed = np.array([(0.1 * 0.2 + (0.1 + 0.1) * 0.2) / 0.4,
                                                 ((0.1 + 0.1) * 0.4 + 0.1 * 1.7) / 2.1])
        # f4(0, 3, 2, 1): (0 -> 3)(2 -> 1)
        branch_true_f4_0321 = (0.1 * 0.2 + (0.1 + 0.1) * 0.6 + 0.1 * 1.7) / 2.5
        # f2([0,2], [1,3]) = (1/2) (f4(0,1,2,3) + f4(0,3,2,1))
        branch_true_f2_02_13 = (branch_true_f4_0123 + branch_true_f4_0321) / 2
        # diversity([0,1,2,3])
        branch_true_diversity_windowed = (2 / 6) * np.array([
                [(0.2 * (1 + 1 + 1 + 0.5 + 0.4 + 0.5) +
                  (0.4 - 0.2) * (0.5 + 0.4 + 0.5 + 0.5 + 0.4 + 0.5)) /
                 0.4],
                [((0.8 - 0.4) * (0.5 + 0.4 + 0.5 + 0.5 + 0.4 + 0.5) +
                  (2.5 - 0.8) * (0.7 + 0.7 + 0.7 + 0.5 + 0.4 + 0.5)) /
                 (2.5 - 0.4)]])

        nodes = io.StringIO("""\
        id      is_sample   time
        0       1           0
        1       1           0
        2       1           0
        3       1           0
        4       0           0.4
        5       0           0.5
        6       0           0.7
        7       0           1.0
        8       0           0.4
        """)
        edges = io.StringIO("""\
        left    right   parent  child
        0.0     2.5     8       1,3
        0.2     0.8     4       0,2
        0.0     0.2     5       8,2
        0.2     0.8     5       8,4
        0.8     2.5     5       8,2
        0.8     2.5     6       0,5
        0.0     0.2     7       0,5
        """)
        sites = io.StringIO("""\
        id  position    ancestral_state
        """)
        mutations = io.StringIO("""\
        site    node    derived_state   parent
        """)
        ts = tskit.load_text(
            nodes=nodes, edges=edges, sites=sites, mutations=mutations,
            strict=False)

        mode = "branch"
        A = [[0], [1], [2], [3]]
        self.assertAlmostEqual(branch_true_f4_0123, f4(ts, A, mode=mode)[0][0])
        self.assertAlmostEqual(branch_true_f4_0123, ts.f4(A, mode=mode)[0][0])
        self.assertArrayAlmostEqual(
            branch_true_f4_0123_windowed,
            ts.f4(A, windows=windows, mode=mode).flatten())
        A = [[0], [3], [2], [1]]
        self.assertAlmostEqual(
            branch_true_f4_0321,
            f4(ts, A, [(0, 1, 2, 3)], mode=mode)[0][0])
        self.assertAlmostEqual(branch_true_f4_0321, ts.f4(A, mode=mode)[0][0])
        A = [[0], [2], [1], [3]]
        self.assertAlmostEqual(0.0, f4(ts, A, [(0, 1, 2, 3)], mode=mode)[0])
        self.assertAlmostEqual(0.0, ts.f4(A, mode=mode)[0][0])
        A = [[0, 2], [1, 3]]
        self.assertAlmostEqual(
            branch_true_f2_02_13, f2(ts, A, [(0, 1)], mode=mode)[0][0])
        self.assertAlmostEqual(branch_true_f2_02_13, ts.f2(A, mode=mode)[0][0])

        # diversity
        A = [[0, 1, 2, 3]]
        self.assertArrayAlmostEqual(
            branch_true_diversity_windowed,
            diversity(ts, A, windows=windows, mode=mode))
        self.assertArrayAlmostEqual(
            branch_true_diversity_windowed,
            ts.diversity(A, windows=windows, mode=mode))

    def test_case_recurrent_muts(self):
        # With mutations:
        #
        # 1.0          6
        # 0.7         / \                                    5
        #           (0)  \                                  /(6)
        # 0.5      (1)    4                4               /   4
        #          /     / \              / \             /  (7|8)
        # 0.4    (2)   (3)  \           (4)  3           /   /   \
        #        /     /     \          /   /(5)        /   /     \
        # 0.0   0     1       2        1   0   2       0   1       2
        #          (0.0, 0.2),        (0.2, 0.8),       (0.8, 1.0)
        # genotypes:
        #       0     2       0        1   0   1       0   2       3
        site_true_Y = 0 + 1 + 1

        nodes = io.StringIO("""\
        id      is_sample   time
        0       1           0
        1       1           0
        2       1           0
        3       0           0.4
        4       0           0.5
        5       0           0.7
        6       0           1.0
        """)
        edges = io.StringIO("""\
        left    right   parent  child
        0.2     0.8     3       0,2
        0.0     0.2     4       1,2
        0.2     0.8     4       1,3
        0.8     1.0     4       1,2
        0.8     1.0     5       0,4
        0.0     0.2     6       0,4
        """)
        sites = io.StringIO("""\
        id  position    ancestral_state
        0   0.05        0
        1   0.3         0
        2   0.9         0
        """)
        mutations = io.StringIO("""\
        site    node    derived_state   parent
        0       0       1               -1
        0       0       2               0
        0       0       0               1
        0       1       2               -1
        1       1       1               -1
        1       2       1               -1
        2       4       1               -1
        2       1       2               6
        2       2       3               6
        """)
        ts = tskit.load_text(
            nodes=nodes, edges=edges, sites=sites, mutations=mutations, strict=False)

        # Y3:
        site_tsc_Y = ts.Y3([[0], [1], [2]], windows=[0.0, 1.0], mode="site")[0][0]
        py_ssc_Y = Y3(ts, [[0], [1], [2]], [(0, 1, 2)], windows=[0.0, 1.0], mode="site")
        self.assertAlmostEqual(site_tsc_Y, site_true_Y)
        self.assertAlmostEqual(py_ssc_Y, site_true_Y)

    def test_case_2(self):
        # Here are the trees:
        # t                  |              |              |             |            |
        #
        # 0       --3--      |     --3--    |     --3--    |    --3--    |    --3--   |
        #        /  |  \     |    /  |  \   |    /     \   |   /     \   |   /     \  |
        # 1     4   |   5    |   4   |   5  |   4       5  |  4       5  |  4       5 |
        #       |\ / \ /|    |   |\   \     |   |\     /   |  |\     /   |  |\     /| |
        # 2     | 6   7 |    |   | 6   7    |   | 6   7    |  | 6   7    |  | 6   7 | |
        #       | |\ /| |    |   *  \  |    |   |  \  |    |  |  \       |  |  \    | |
        # 3     | | 8 | |    |   |   8 *    |   |   8 |    |  |   8      |  |   8   | |
        #       | |/ \| |    |   |  /  |    |   |  /  |    |  |  / \     |  |  / \  | |
        # 4     | 9  10 |    |   * 9  10    |   | 9  10    |  | 9  10    |  | 9  10 | |
        #       |/ \ / \|    |   |  \   \   |   |  \   \   |  |  \   \   |  |  \    | |
        # 5     0   1   2    |   0   1   2  |   0   1   2  |  0   1   2  |  0   1   2 |
        #
        #                    |   0.0 - 0.1  |   0.1 - 0.2  |  0.2 - 0.4  |  0.4 - 0.5 |
        # ... continued:
        # t                  |             |             |             |
        #
        # 0         --3--    |    --3--    |    --3--    |    --3--    |    --3--
        #          /     \   |   /     \   |   /     \   |   /     \   |   /  |  \
        # 1       4       5  |  4       5  |  4       5  |  4       5  |  4   |   5
        #         |\     /|  |   \     /|  |   \     /|  |   \     /|  |     /   /|
        # 2       | 6   7 |  |    6   7 |  |    6   7 |  |    6   7 |  |    6   7 |
        #         |  *    *  |     \    |  |       *  |  |    |  /  |  |    |  /  |
        # 3  ...  |   8   |  |      8   |  |      8   |  |    | 8   |  |    | 8   |
        #         |  / \  |  |     / \  |  |     * \  |  |    |  \  |  |    |  \  |
        # 4       | 9  10 |  |    9  10 |  |    9  10 |  |    9  10 |  |    9  10 |
        #         |    /  |  |   /   /  |  |   /   /  |  |   /   /  |  |   /   /  |
        # 5       0   1   2  |  0   1   2  |  0   1   2  |  0   1   2  |  0   1   2
        #
        #         0.5 - 0.6  |  0.6 - 0.7  |  0.7 - 0.8  |  0.8 - 0.9  |  0.9 - 1.0
        #
        # Above, subsequent mutations are backmutations.

        # divergence betw 0 and 1
        branch_true_diversity_01 = 2*(0.6*4 + 0.2*2 + 0.2*5)
        # divergence betw 1 and 2
        branch_true_diversity_12 = 2*(0.2*5 + 0.2*2 + 0.3*5 + 0.3*4)
        # divergence betw 0 and 2
        branch_true_diversity_02 = 2*(0.2*5 + 0.2*4 + 0.3*5 + 0.1*4 + 0.2*5)
        # Y(0;1, 2)
        branch_true_Y = 0.2*4 + 0.2*(4+2) + 0.2*4 + 0.2*2 + 0.2*(5+1)

        # site stats
        # Y(0;1, 2)
        site_true_Y = 1

        nodes = io.StringIO("""\
        is_sample       time    population
        1       0.000000        0
        1       0.000000        0
        1       0.000000        0
        0       5.000000        0
        0       4.000000        0
        0       4.000000        0
        0       3.000000        0
        0       3.000000        0
        0       2.000000        0
        0       1.000000        0
        0       1.000000        0
        """)
        edges = io.StringIO("""\
        left    right   parent  child
        0.500000        1.000000        10      1
        0.000000        0.400000        10      2
        0.600000        1.000000        9       0
        0.000000        0.500000        9       1
        0.800000        1.000000        8       10
        0.200000        0.800000        8       9,10
        0.000000        0.200000        8       9
        0.700000        1.000000        7       8
        0.000000        0.200000        7       10
        0.800000        1.000000        6       9
        0.000000        0.700000        6       8
        0.400000        1.000000        5       2,7
        0.100000        0.400000        5       7
        0.600000        0.900000        4       6
        0.000000        0.600000        4       0,6
        0.900000        1.000000        3       4,5,6
        0.100000        0.900000        3       4,5
        0.000000        0.100000        3       4,5,7
        """)
        sites = io.StringIO("""\
        id  position    ancestral_state
        0   0.0         0
        1   0.55        0
        2   0.75        0
        3   0.85        0
        """)
        mutations = io.StringIO("""\
        site    node    derived_state   parent
        0       0       1               -1
        0       10      1               -1
        0       0       0               0
        1       8       1               -1
        1       2       1               -1
        2       8       1               -1
        2       9       0               5
        """)
        ts = tskit.load_text(
            nodes=nodes, edges=edges, sites=sites, mutations=mutations,
            strict=False)

        def f(x):
            return np.array([float(x[0] == 1)/2.0])

        # divergence between 0 and 1
        mode = "branch"
        for A, truth in zip(
                [[[0, 1]], [[1, 2]], [[0, 2]]],
                [branch_true_diversity_01,
                 branch_true_diversity_12,
                 branch_true_diversity_02]):

            self.assertAlmostEqual(diversity(ts, A, mode=mode)[0][0], truth)
            self.assertAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0], truth)
            self.assertAlmostEqual(ts.diversity(A, mode="branch")[0][0], truth)

        # Y-statistic for (0/12)
        A = [[0], [1, 2]]

        def f(x):
            return np.array([float(((x[0] == 1) and (x[1] == 0))
                                   or ((x[0] == 0) and (x[1] == 2)))/2.0])

        # tree lengths:
        self.assertArrayAlmostEqual(Y3(ts, [[0], [1], [2]], [(0, 1, 2)], mode=mode),
                                    branch_true_Y)
        self.assertArrayAlmostEqual(ts.Y3([[0], [1], [2]], [(0, 1, 2)], mode=mode),
                                    branch_true_Y)
        self.assertArrayAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0],
                                    branch_true_Y)

        # sites:
        mode = "site"
        site_tsc_Y = ts.Y3([[0], [1], [2]], windows=[0.0, 1.0], mode=mode)[0][0]
        py_ssc_Y = Y3(ts, [[0], [1], [2]], [(0, 1, 2)], windows=[0.0, 1.0])
        self.assertAlmostEqual(site_tsc_Y, site_true_Y)
        self.assertAlmostEqual(py_ssc_Y, site_true_Y)
        self.assertAlmostEqual(ts.sample_count_stat(A, f, mode=mode)[0][0], site_true_Y)


############################################
# Old code where stats are defined within type
# specific calculattors. These definititions have been
# move to stat-specific regions above
# The only thing left to port is the SFS code.
############################################

class PythonBranchStatCalculator(object):
    """
    Python implementations of various ("tree") branch-length statistics -
    inefficient but more clear what they are doing.
    """

    def __init__(self, tree_sequence):
        self.tree_sequence = tree_sequence

    def site_frequency_spectrum(self, sample_set, windows=None):
        if windows is None:
            windows = [0.0, self.tree_sequence.sequence_length]
        n_out = len(sample_set)
        out = np.zeros((n_out, len(windows) - 1))
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = [0.0 for j in range(n_out)]
            for t in self.tree_sequence.trees(tracked_samples=sample_set,
                                              sample_counts=True):
                root = t.root
                tr_len = min(end, t.interval[1]) - max(begin, t.interval[0])
                if tr_len > 0:
                    for node in t.nodes():
                        if node != root:
                            x = t.num_tracked_samples(node)
                            if x > 0:
                                S[x - 1] += t.branch_length(node) * tr_len
            for j in range(n_out):
                S[j] /= (end-begin)
            out[j] = S
        return(out)


class PythonSiteStatCalculator(object):
    """
    Python implementations of various single-site statistics -
    inefficient but more clear what they are doing.
    """

    def __init__(self, tree_sequence):
        self.tree_sequence = tree_sequence

    def sample_count_stats(self, sample_sets, f, windows=None, polarised=False):
        '''
        Here sample_sets is a list of lists of samples, and f is a function
        whose argument is a list of integers of the same length as sample_sets
        that returns a list of numbers; there will be one output for each element.
        For each value, each allele in a tree is weighted by f(x), where
        x[i] is the number of samples in sample_sets[i] that inherit that allele.
        This finds the sum of this value for all alleles at all polymorphic sites,
        and across the tree sequence ts, weighted by genomic length.

        This version is inefficient as it works directly with haplotypes.
        '''
        if windows is None:
            windows = [0.0, self.tree_sequence.sequence_length]
        for U in sample_sets:
            if max([U.count(x) for x in set(U)]) > 1:
                raise ValueError("elements of sample_sets",
                                 "cannot contain repeated elements.")
        haps = list(self.tree_sequence.haplotypes())
        n_out = len(f([0 for a in sample_sets]))
        out = np.zeros((n_out, len(windows) - 1))
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            site_positions = [x.position for x in self.tree_sequence.sites()]
            S = [0.0 for j in range(n_out)]
            for k in range(self.tree_sequence.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    all_g = [haps[j][k] for j in range(self.tree_sequence.num_samples)]
                    g = [[haps[j][k] for j in u] for u in sample_sets]
                    for a in set(all_g):
                        x = [h.count(a) for h in g]
                        w = f(x)
                        for j in range(n_out):
                            S[j] += w[j]
            for j in range(n_out):
                S[j] /= (end - begin)
            out[j] = np.array([S])
        return out

    def naive_general_stat(self, W, f, windows=None, polarised=False):
        return naive_site_general_stat(
            self.tree_sequence, W, f, windows=windows, polarised=polarised)

    def site_frequency_spectrum(self, sample_set, windows=None):
        if windows is None:
            windows = [0.0, self.tree_sequence.sequence_length]
        haps = list(self.tree_sequence.haplotypes())
        site_positions = [x.position for x in self.tree_sequence.sites()]
        n_out = len(sample_set)
        out = np.zeros((n_out, len(windows) - 1))
        for j in range(len(windows) - 1):
            begin = windows[j]
            end = windows[j + 1]
            S = [0.0 for j in range(n_out)]
            for k in range(self.tree_sequence.num_sites):
                if (site_positions[k] >= begin) and (site_positions[k] < end):
                    all_g = [haps[j][k] for j in range(self.tree_sequence.num_samples)]
                    g = [haps[j][k] for j in sample_set]
                    for a in set(all_g):
                        x = g.count(a)
                        if x > 0:
                            S[x - 1] += 1.0
            for j in range(n_out):
                S[j] /= (end - begin)
            out[j] = S
        return out
