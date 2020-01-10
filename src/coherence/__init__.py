import platform

__version_info__ = (0,6,4)
__version__ = '%d.%d.%d' % (__version_info__[0],__version_info__[1],__version_info__[2],)

SERVER_ID = ','.join([platform.system(),
                      platform.release(),
                      'UPnP/1.0,Coherence UPnP framework',
                      __version__])
