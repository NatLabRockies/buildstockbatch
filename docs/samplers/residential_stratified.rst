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
- ``segment_vars`` (optional): The housing characteristics whose combination defines a segment.
  Each should be a characteristic the allocation step later joins on; a characteristic that only
  flows downhill through the TSVs (such as ``Geometry Floor Area Bin``) does not belong here.
  Default is:

  - Federal Poverty Level
  - Geometry Building Type RECS
  - Vintage
  - Heating Fuel
  - Sampling Region

- ``segment_selection_sample_size`` (optional): Size of the initial sample used to rank segments
  by how much of the stock they cover. Default is 10000000.
- ``num_samples_per_segment`` (optional): Buildings kept per segment. This one value sets both how
  many segments are kept (``n_datapoints // num_samples_per_segment``) and how many buildings are
  taken from each, so the sample holds at most ``n_datapoints`` buildings and fewer when a segment
  holds fewer than the take. Default is 12.
