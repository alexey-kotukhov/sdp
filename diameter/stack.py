from twisted.internet.protocol import Factory,Protocol
from twisted.internet import reactor
from twisted.python import log
import sys, warnings, time
from diameter import dictionary
from diameter.peer import PeerStateMachine, PeerManager
from diameter.protocol import DiameterMessage, DiameterAVP
import logging
_log = logging.getLogger("sdp.diameter.stack")

class PeerListener:
    def __init__(self):
        pass

    def added(self, peer):
        pass

    def removed(self, peer):
        pass

    def connected(self, peer):
        pass

    def disconnected(self, peer):
        pass

class ApplicationListener:
    def __init__(self):
        pass

    def setStack(self, stack):
        self.stack = stack

    def onRequest(self, peer, request):
        pass

    def onAnswer(self, peer, answer):
        pass

    def onRedirect(self, peer, request):
        pass

    def onRetransmit(self, peer, request):
        """If no special treatment is required, map it to onRequest"""
        pass

    def onTick(self):
        """Called on each stack tick"""
        pass


class Stack:
    def __init__(self, product_name="nuswit diameter", ip4_address="127.0.0.1"):
        self.applications = dict()
        self.peer_listeners = list()
        self.dictionaries = dict()
        self.manager = PeerManager(self)
        self.product_name = product_name
        self.ip4_address = ip4_address
        self.vendor_id = 0
        self.supported_vendors = list()
        self.firmware_revision = 1
        self.watchdog_seconds = None
        self.hbh = 0
        self.ete = 0

        self.queued_messages = []

        self.identity = None
        self.realm = None

    def nextHbH(self):
        self.hbh += 1
        return self.hbh

    def nextEtE(self):
        self.ete += 1
        return self.ete

    def createRequest(self, application, code, auth=False, acct=False, vendor_id=None):
        _log.debug("Creating Diameter message with command code %d", code)
        ret = DiameterMessage()
        ret.request_flag = True
        ret.eTe = self.nextEtE()
        ret.hBh = self.nextHbH()
        ret.application_id = application
        ret.command_code = code

        origin_host = DiameterAVP()
        origin_host.setCode(264)
        origin_host.setMandatory(True)
        origin_host.setOctetString(self.identity)
        ret.addAVP(origin_host)

        origin_realm = DiameterAVP()
        origin_realm.setCode(296)
        origin_realm.setMandatory(True)
        origin_realm.setOctetString(self.realm)
        ret.addAVP(origin_realm)

        if vendor_id:
            app_container = DiameterAVP()
            app_container.setCode(260)
            app_container.setMandatory(True)
            tmp = DiameterAVP()
            tmp.setCode(266)
            tmp.setMandatory(True)
            tmp.setInteger32(vendor_id)
            app_container.addAVP(tmp)
        else:
            app_container = ret

        if auth:
            tmp = DiameterAVP()
            tmp.setCode(258)
            tmp.setMandatory(True)
            tmp.setInteger32(application)
            app_container.addAVP(tmp)
        elif acct:
            tmp = DiameterAVP()
            tmp.setCode(258)
            tmp.setMandatory(True)
            tmp.setInteger32(application)
            app_container.addAVP(tmp)

        if app_container != ret:
            ret.addAVP(app_container)

        return ret


    def loadDictionary(self, dict_name, dict_file):
        self.dictionaries[dict_name] = dictionary.DiameterDictionary(dict_file)

    def getDictionary(self, dict_name):
        return self.dictionaries[dict_name]

    def addSupportedVendor(self, vendor):
        self.supported_vendors.append(vendor)

    def registerApplication(self, app, vendor, code):
        self.applications[(vendor,code)] = app

    def registerPeerListener(self, pl):
        self.peer_listeners.append(pl)

    def registerPeerIO(self, pio):
        self.manager.registerPeerIO(pio)

    def clientV4Add(self, host, port):
        return self.manager.clientV4Add(host, port)

    def serverV4Add(self, host, port):
        return self.manager.serverV4Add(host, port)

    def serverV4Accept(self, base_peer, host, port):
        return self.manager.serverV4Accept(base_peer, host, port)

    def sendByPeer(self, peer, message, retransmission=True):
        if message.request_flag and retransmission:
            self.queued_messages.append((peer,message))
        self.manager.send(peer, message)

    def registerPeer(self, peer, identity, realm, apps):
        r = self.manager.registerPeer(peer, identity, realm, apps)
        _log.info("Registering peer %s with identity %s for realm %s with apps %s",
                   peer,
                   identity,
                   realm,
                   apps)
        if r == True:
            _log.info("Successfully registered %s", peer)
            for p in self.peer_listeners:
                if peer.peer_type == PeerStateMachine.PEER_CLIENT:
                    p.connected(peer)
                else:
                    p.added(peer)
            return True
        else:
            #error, duplicated peer or something like that
            _log.error("Failed to register %s", peer)
            return False

    def handleIncomingMessage(self, peer, message):
        _log.debug("Handling incoming Diameter message from peer %s", peer)

        # first check for a vendor-specific application id
        vendorid = 0
        rapp_container = message.findFirstAVP(260)
        if rapp_container != None:
            rvendorid = rapp_container.findFirstAVP(266)
            if rvendorid != None:
                vendorid = rvendorid.getInteger32()
        else:
            rapp_container = message
        
        # look for auth/application ids
        rapp = rapp_container.findFirstAVP(258)
        if rapp == None:
            rapp = rapp_container.findFirstAVP(259)

        if rapp != None:
            rvalue = rapp.getInteger32()
        else:
            rvalue = message.application_id

        try:
            app = self.applications[(vendorid,rvalue)]
        except:
            _log.error("Peer %s: Application (%d,%d) not found" % (peer, vendorid, rvalue))
            if message.request_flag:
                answ = message.createAnswer()
                answ.error_flag = True
                self.sendByPeer(peer, answ)
            return

        if message.request_flag:
            app.onRequest(peer, message)
        else:
            #remove from retransmission queue
            self.queued_messages[:] = [x for x in self.queued_messages if not x[1].hBh == message.hBh]
            app.onAnswer(peer, message)

    def tick(self):
        """Check retransmissions"""
        self.queued_messages[:] = [x for x in self.queued_messages if self.dispatch_messages(*x)]
        #tick all applications ( required so tick is called only once )
        apps = list(set(self.applications.values()))
        for app in apps:
            app.onTick()

    def dispatch_messages(self, peer, msg):
        now = int(time.time())
        #3 seconds, 3 retries ( one per sec )
        if msg.last_try < now - 1:
            if msg.retries < 3:
                _log.debug("Sending message to peer %s, attempt number %d", peer, msg.retries)
                self.manager.send(peer,msg)
                return True
            else:
                _log.error("Failed to send message to peer %s, after %d retries", peer, msg.retries)
                return False
