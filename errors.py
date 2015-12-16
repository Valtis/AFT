# Copyright (c) 2013-2015 Intel, Inc.
# Author Topi Kuutela <topi.kuutela@intel.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; version 2 of the License
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.

"""
A central location for all AFT errors
"""

class AFTConfigurationError(Exception):
    """
    An error caused by incorrect configuration
    """
    pass

class AFTConnectionError(Exception):
    """
    An error caused by failed (SSH) connection
    """
    pass

class AFTTimeoutError(Exception):
    """
    A timeout error
    """
    pass

class AFTDeviceError(Exception):
    """
    An error caused by device under test
    """
    pass