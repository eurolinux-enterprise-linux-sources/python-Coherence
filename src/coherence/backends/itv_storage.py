# Licensed under the MIT license
# http://opensource.org/licenses/mit-license.php

# Backend to retrieve the video streams from Shoutcast TV

# Copyright 2007, Frank Scholz <coherence@beebits.net>
# Copyright 2008, Jean-Michel Sizun <jmDOTsizunATfreeDOTfr>

from twisted.internet import defer
from twisted.web import server

from coherence.upnp.core import utils

from coherence.upnp.core import DIDLLite

from coherence.extern.simple_plugin import Plugin

from coherence import log
from coherence.backend import BackendItem, BackendStore

import zlib

from coherence.backend import BackendStore,BackendItem


ROOT_CONTAINER_ID = 0

SHOUTCAST_WS_URL = 'http://www.shoutcast.com/sbin/newtvlister.phtml?service=winamp2&no_compress=1'
SHOUTCAST_TUNEIN_URL = 'http://www.shoutcast.com/sbin/tunein-tvstation.pls?id=%s'
VIDEO_MIMETYPE = 'video/x-nsv'

class ProxyStream(utils.ReverseProxyUriResource):

    stream_url = None

    def __init__(self, uri):
        self.stream_url = None
        utils.ReverseProxyUriResource.__init__(self, uri)

    def requestFinished(self, result):
        """ self.connection is set in utils.ReverseProxyResource.render """
        print "ProxyStream requestFinished"
        if self.connection is not None:
            self.connection.transport.loseConnection()

    def render(self, request):

        if self.stream_url is None:
            def got_playlist(result):
                if result is None:
                    print 'Error to retrieve playlist - nothing retrieved'
                    return requestFinished(result)
                result = result[0].split('\n')
                for line in result:
                    if line.startswith('File1='):
                        self.stream_url = line[6:].split(";")[0]
                        break
                if self.stream_url is None:
                    print 'Error to retrieve playlist - inconsistent playlist file'
                    return requestFinished(result)
                #self.resetUri(self.stream_url)
                request.uri = self.stream_url
                return self.render(request)

            def got_error(error):
                print error
                return None

            playlist_url = self.uri
            d = utils.getPage(playlist_url, timeout=20)
            d.addCallbacks(got_playlist, got_error)
            return server.NOT_DONE_YET

        if request.clientproto == 'HTTP/1.1':
            connection = request.getHeader('connection')
            if connection:
                tokens = map(str.lower, connection.split(' '))
                if 'close' in tokens:
                    d = request.notifyFinish()
                    d.addBoth(self.requestFinished)
        else:
            d = request.notifyFinish()
            d.addBoth(self.requestFinished)
        return utils.ReverseProxyUriResource.render(self, request)


class Container(BackendItem):

    def __init__(self, id, store, parent_id, title):
        self.url = store.urlbase+str(id)
        self.parent_id = parent_id
        self.id = id
        self.name = title
        self.mimetype = 'directory'
        self.update_id = 0
        self.children = []
        self.store = store

        self.item = DIDLLite.Container(self.id, self.parent_id, self.name)
        self.item.childCount = 0

        self.sorted = False

    def add_child(self, child):
        id = child.id
        if isinstance(child.id, basestring):
            _,id = child.id.split('.')
        if self.children is None:
            self.children = []
        self.children.append(child)
        self.item.childCount += 1
        self.sorted = False

    def get_children(self, start=0, end=0):
        if self.sorted == False:
            def childs_sort(x,y):
                r = cmp(x.name,y.name)
                return r

            self.children.sort(cmp=childs_sort)
            self.sorted = True
        if end != 0:
            return self.children[start:end]
        return self.children[start:]

    def get_child_count(self):
        if self.children is None:
            return 0
        return len(self.children)

    def get_path(self):
        return self.url

    def get_item(self):
        return self.item

    def get_name(self):
        return self.name

    def get_id(self):
        return self.id


class ITVItem(BackendItem):
    logCategory = 'itv_item'
    
    def __init__(self, store, id, obj, parent):
        self.parent = parent
        self.id = id
        self.name = obj.get('name')
        self.mimetype = obj.get('mimetype')
        self.description = None
        self.date = None
        self.item = None
        self.duration = None
        self.store = store
        self.url = self.store.urlbase + str(self.id)
        self.location = ProxyStream(obj.get('url'))

    def get_item(self):
        if self.item == None:
            self.item = DIDLLite.AudioItem(self.id, self.parent.id, self.name)
            self.item.description = self.description
            self.item.date = self.date
            res = DIDLLite.Resource(self.url, 'http-get:*:%s:*' % self.mimetype)
            res.duration = self.duration
            #res.size = 0 #None
            self.item.res.append(res)
        return self.item

    def get_path(self):
        return self.url




class ITVStore(BackendStore):

    logCategory = 'iradio_store'

    implements = ['MediaServer']

    def __init__(self, server, **kwargs):
        BackendStore.__init__(self,server,**kwargs)
        self.next_id = 1000
        self.config = kwargs
        self.name = kwargs.get('name','iTV')

        self.update_id = 0
        self.store = {}

        self.wmc_mapping = {'4': 1000}


        self.shoutcast_ws_url = self.config.get('genrelist',SHOUTCAST_WS_URL)

        self.init_completed()


    def __repr__(self):
        return str(self.__class__).split('.')[-1]

    def storeItem(self, parent, item, id):
        self.store[id] = item
        parent.add_child(item)

    def appendGenre( self, genre, parent):
        id = self.getnextID()
        item = Container(id, self, -1, genre)
        self.storeItem(parent, item, id)
        return item

    def appendFeed( self, obj, parent):
        id = self.getnextID()
        item = ITVItem(self, id, obj, parent)
        self.storeItem(parent, item, id)
        return item


    def len(self):
        return len(self.store)

    def get_by_id(self,id):
        if isinstance(id, basestring):
            id = id.split('@',1)
            id = id[0]
        try:
            return self.store[int(id)]
        except (ValueError,KeyError):
            pass
        return None

    def getnextID(self):
        ret = self.next_id
        self.next_id += 1
        return ret

    def upnp_init(self):
        self.current_connection_id = None

        if self.server:
            self.server.connection_manager_server.set_variable(0, 'SourceProtocolInfo',
                                                                    ['http-get:*:%s:*' % VIDEO_MIMETYPE,
                                                                     ],
                                                                   default=True)
        rootItem = Container(ROOT_CONTAINER_ID,self,-1, self.name)
        self.store[ROOT_CONTAINER_ID] = rootItem
        self.retrieveList(rootItem)

    def retrieveList(self, parent):

        def got_page(result):
            print "connection to ShoutCast service successful for TV listing"
            result = result[0]
            result = utils.parse_xml(result, encoding='utf-8')

            genres = []
            stations = {}
            for stationResult in result.findall('station'):
                mimetype = VIDEO_MIMETYPE
                station_id = stationResult.get('id')
                bitrate = stationResult.get('br')
                rating = stationResult.get('rt')
                name = stationResult.get('name').encode('utf-8')
                genre = stationResult.get('genre')
                url = SHOUTCAST_TUNEIN_URL % (station_id)

                if genres.count(genre) == 0:
                    genres.append(genre)

                sameStation = stations.get(name)
                if sameStation == None or bitrate>sameStation['bitrate']:
                    station = {'name':name,
                               'station_id':station_id,
                               'mimetype':mimetype,
                               'id':station_id,
                               'url':url,
                               'bitrate':bitrate,
                               'rating':rating,
                               'genre':genre }
                    stations[name] = station


            genreItems = {}
            for genre in genres:
                genreItem = self.appendGenre(genre, parent)
                genreItems[genre] = genreItem

            for station in stations.values():
                genre = station.get('genre')
                parentItem = genreItems[genre]
                self.appendFeed({'name':station.get('name'),
                                    'mimetype':station['mimetype'],
                                    'id':station.get('station_id'),
                                    'url':station.get('url')},
                            parentItem)


        def got_error(error):
            print ("connection to ShoutCast service failed! %r" % error)
            self.debug("%r", error.getTraceback())

        d = utils.getPage(self.shoutcast_ws_url)
        d.addCallbacks(got_page, got_error)
