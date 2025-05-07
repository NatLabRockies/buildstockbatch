.. |version| replace:: development

=======================================
What's new in buildstockbatch |version|
=======================================

.. admonition:: About this Document

    This document describes changes between buildstockbatch version 2022.11.0 and
    buildstockbatch version |version|

General
=======

This version should be backwards compatible with previous versions of
buildstockbatch.

See :doc:`changelog_dev` for details of this change.

New Flags
=========
A new flag ``low-disk`` has been added to delete unused results and minimize hard-drive space used by buildstockbatch.

.. code-block::

    options:
    --low-disk           Delete unused simulation result files immediately after processing to save disk space.

Schema Updates
==============

Add as changes are made.
