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

Arguments
~~~~~~~~~

- ``n_datapoints``: The number of datapoints to sample.
