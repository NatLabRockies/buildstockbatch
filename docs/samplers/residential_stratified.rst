Residential Stratified Sampler
------------------------------

The Residential Stratfied sampler utilizes a `stratified-based sampling method <TODO>`_ to determine the buildings to simulate. It is the primary sampling algorithm used in ResStock.

Configuration Example
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

    sampler:
      type: residential_stratified
      args:
        n_datapoints: 350000
        segment_vars:
          - Vintage
          - Heating Fuel
          - Sampling Region
        segment_selection_sample_size: 5000000
        num_samples_per_segment: 10

Arguments
~~~~~~~~~

- ``n_datapoints``: The number of datapoints to sample.
- ``segment_vars`` (optional): TODO The segment variables. Default is:

  - Federal Poverty Level
  - Geometry Floor Area Bin
  - Geometry Building Type RECS
  - Vintage
  - Heating Fuel
  - Sampling Region

- ``segment_selection_sample_size`` (optional): TODO The segment selection sample size. Default is 10000000.
- ``num_samples_per_segment`` (optional): TODO The number of samples per segment. Default is 8.
