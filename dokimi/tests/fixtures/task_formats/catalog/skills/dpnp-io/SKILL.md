---
name: dpnp-io
description: >-
  Reading and writing files from dpnp code on Intel CPUs and GPUs. Use when the
  user needs to load an array into dpnp or save a dpnp result — .npy, .npz, HDF5
  via h5py, Zarr, CSV or plain text — when a file is larger than device memory and
  has to be read in chunks, or when they ask why dpnp has no save function of its
  own. Covers the NumPy conversion round trip, chunked and incremental patterns,
  and choosing a format by dataset size.
license: Apache-2.0
compatibility: "Requires dpnp and NumPy. HDF5 needs h5py, Zarr needs zarr, CSV parsing examples use pandas."
metadata:
