from diameter.protocol import DiameterMessage, DiameterAVP
import struct
import logging

_log = logging.getLogger("sdp.diameter.peer")


class PeerStateMachine:
    # starts 'sending' CER
    PEER_CLIENT = 0
    # starts 'waiting' for CER
    PEER_SERVER = 1
    # just accepts connections
    PEER_LISTEN = 2

    def __init__(self, peer, peer_type):
        self.peer = peer
        self.stack = peer.stack
        if peer_type == PeerStateMachine.PEER_CLIENT:
            self.run = self.send_cer
        elif peer_type == PeerStateMachine.PEER_SERVER:
            self.run = self.receive_cer
        elif peer_type == PeerStateMachine.PEER_LISTEN:
            self.run = None

    def send_cer(self, consumed, message):
        msg = self.stack.createRequest(0, 257)
        # vendorid
        tmp = DiameterAVP()
        tmp.setCode(266)
        tmp.setMandatory(True)
        tmp.setInteger32(self.stack.vendor_id)
        msg.addAVP(tmp)

        # productname
        tmp = DiameterAVP()
        tmp.setCode(269)
        tmp.setMandatory(True)
        tmp.setOctetString(self.stack.product_name)
        msg.addAVP(tmp)

        # firmware
        tmp = DiameterAVP()
        tmp.setCode(267)
        tmp.setMandatory(True)
        tmp.setInteger32(self.stack.firmware_revision)
        msg.addAVP(tmp)

        # host ip
        tmp = DiameterAVP()
        tmp.setCode(257)
        tmp.setMandatory(True)
        tmp.setIPV4(self.stack.ip4_address)
        msg.addAVP(tmp)

        # get supported vendors from stack
        for vendor in self.stack.supported_vendors:
            supp = DiameterAVP()
            supp.setCode(265)
            supp.setMandatory(True)
            supp.setInteger32(vendor)
            msg.addAVP(supp)

        # get applications from stack
        for apps in [self.stack.auth_apps, self.stack.acct_apps]:
            for app in apps:
                # Build *-Application-Id AVP
                app_id = DiameterAVP()
                if apps == self.stack.auth_apps:
                    # App is for authentication, so use Auth-Application-Id AVP code
                    app_id.setCode(258)
                    _log.debug("CER Auth-Application-Id %d", app[1])
                else:
                    # App is for accounting, so use Acct-Application-Id AVP code
                    app_id.setCode(259)
                    _log.debug("CER Acct-Application-Id %d", app[1])
                app_id.setMandatory(True)
                app_id.setInteger32(app[1])

                if app[0]:
                    tmp = DiameterAVP()
                    tmp.setCode(260)
                    tmp.setMandatory(True)
                    # vendor
                    v = DiameterAVP()
                    v.setCode(266)
                    v.setMandatory(True)
                    v.setInteger32(app[0])
                    tmp.addAVP(v)
                    tmp.addAVP(app_id)
                    msg.addAVP(tmp)
                else:
                    msg.addAVP(app_id)

        _log.debug("Send CEA")
        self.stack.sendByPeer(self.peer, msg, False)

        self.run = self.receive_cea

    def receive_cea(self, consumed, message):
        _log.info("Received CEA from peer %s", self.peer)
        # check Result-Code
        tmp = message.findFirstAVP(268)
        # missing result code!
        if tmp == None:
            _log.error("CEA from peer %s has no result code", self.peer)

        result = tmp.getInteger32()
        _log.debug("CEA from peer %s has result code %d", self.peer, result)

        # register peer!
        if result == 2001:
            tmp = message.findFirstAVP(264)
            if tmp == None:
                _log.error("CEA from peer %s has no Origin-Host AVP", self.peer)

            identity = tmp.getOctetString()

            tmp = message.findFirstAVP(296)
            if tmp == None:
                _log.error("CEA from peer %s has no Origin-Realm AVP", self.peer)

            realm = tmp.getOctetString()

            apps = dict()
            for auth in message.findAVP(258):
                v = auth.getInteger32()
                _log.debug("CEA from peer %s has Auth-Application-Id %d", self.peer, v)

                if not apps.has_key((0, v)):
                    apps[(0, v)] = True

            for acct in message.findAVP(259):
                v = acct.getInteger32()
                if not apps.has_key((0, v)):
                    apps[(0, v)] = True

            vtmp = message.findAVP(260)
            for vendor in vtmp:
                vid = vendor.findFirstAVP(266).getInteger32()
                acct = vendor.findFirstAVP(259)
                auth = vendor.findFirstAVP(258)
                tmp = message.findAVP(259)

                if auth and not apps.has_key((vid, auth.getInteger32())):
                    apps[(vid, auth.getInteger32())] = True
                if acct and not apps.has_key((vid, acct.getInteger32())):
                    apps[(vid, acct.getInteger32())] = True

            _log.debug("CEA from peer %s has identity %s, realm %s and apps %s", self.peer, identity, realm, apps)
            if self.stack.registerPeer(self.peer, identity, realm, apps):
                self.run = self.app_handler
            else:
                _log.error("registerPeer failed on CEA from peer %s with identity %s, realm %s and apps %s", self.peer,
                           identity, realm, apps)

    def app_handler(self, consumed, message):
        """
        Watch out for application-Id 0
        DPR/DWR will also show up in here
        """
        if not message:
            return

        # watchdog, don't send it up the stack
        if message.application_id == 0 and \
                        message.command_code == 280:
            _log.debug("Received Device-Watchdog message from peer %s", self.peer)

            if message.request_flag:
                _log.debug("Received Device-Watchdog-Request message from peer %s, replying", self.peer)
                answ = self.stack.createAnswer(message, 2001)
                self.stack.sendByPeer(self.peer, answ, False)
            return

        self.stack.handleIncomingMessage(self.peer, message)

    def receive_cer(self, consumed, message):
        if not message:
            return
        _log.info("Received CER from peer %s", self.peer)
        _log.debug("AVP: %s", message)

        tmp = message.findFirstAVP(264)
        if tmp == None:
            _log.error("CEA from peer %s has no Origin-Host AVP", self.peer)

        identity = tmp.getOctetString()

        tmp = message.findFirstAVP(296)
        if tmp == None:
            _log.error("CEA from peer %s has no Origin-Realm AVP", self.peer)

        realm = tmp.getOctetString()

        apps = dict()

        reply = self.stack.createAnswer(message, 2001)

        for appId in message.findAVP(258):
            if (0, appId.getInteger32()) in self.stack.auth_apps:
                reply.addAVP(appId)
                apps[(0, appId.getInteger32())] = True
                _log.debug("Add auth CEA %s", appId)
        for appId in message.findAVP(259):
            if (0, appId.getInteger32()) in self.stack.acct_apps:
                reply.addAVP(appId)
                apps[(0, appId.getInteger32())] = True
                _log.debug("Add acct CEA %s", appId)
        self.stack.sendByPeer(self.peer, reply, False)
        if self.stack.registerPeer(self.peer, identity, realm, apps):
            self.run = self.app_handler
        else:
            _log.error("registerPeer failed on CEA from peer %s with identity %s, realm %s and apps %s", self.peer,
                       identity, realm, apps)


class Peer:
    def __init__(self, manager, peer_type):
        self.applications = None
        self.manager = manager
        self.stack = manager.stack
        self.identity = None
        self.realm = None
        self.last_watchdog = None
        self.next_tick = None
        self.state = None
        self.ipv4 = None
        self.port = None
        self.peer_type = peer_type
        self.fsm = PeerStateMachine(self, peer_type)
        pass

    def __str__(self):
        return "<diameter.peer.Peer instance" \
               " identity=%s," \
               " realm=%s" \
               " ipv4=%s>" \
               % (self.identity, self.realm, self.ipv4)

    def feed(self, buf, length):
        """Returns the amount of bytes consumed from buf"""
        total_consumed = 0

        # special signal, send it up the stack
        if length == 0:
            self.fsm.run(0, None)
            return 0

        # read error, disconnect
        if length == -1:
            self.fsm.run(-1, None)
            return -1

        # while we have an entire diameter header
        while length >= 20:
            version_length = struct.unpack("!i", buf[:4])[0]
            version = version_length >> 24
            msg_length = (version_length & 0x00ffffff)

            # protocol error, disconnect
            if version != 1:
                pass

            # can't read one entire message
            # caller should buffer
            if msg_length > length:
                return total_consumed

            msg = DiameterMessage()
            consumed = msg.parseFromBuffer(buf)
            self.fsm.run(consumed, msg)

            # protocol error, disconnect
            if consumed <= 0:
                return consumed

            # remove the handled message ready to go round again
            buf = buf[consumed:]
            length -= consumed
            total_consumed += consumed

        return total_consumed

    def destroy(self):
        pass


class Realm:
    def __init__(self):
        self.name = None
        self.applications = dict()
        self.identities = dict()

    def addPeer(self, peer, identity, apps):
        """Add identity, add application"""
        if self.identities.has_key(identity):
            _log.error("Identity %s already in identities for realm %s with value %s (tried to set to %s)",
                       identity,
                       self.name,
                       self.identities[identity],
                       peer)
            return False

        self.identities[identity] = peer

        for app in apps:
            if self.applications.has_key(app):
                appentry = self.applications[app]
            else:
                appentry = list()
                self.applications[app] = appentry
            appentry.append(peer)

        _log.debug("Added identity %s to realm %s as peer %s",
                   identity,
                   self.name,
                   peer)
        return True

    def removePeer(self, peer):
        self.identities.pop(peer.identity, None)
        for app in self.applications.values():
            try:
                app.remove(peer)
            except ValueError:
                pass
        _log.debug("Remover identity %s to realm %s as peer %s",
                   peer.identity, self.name, peer)




class PeerIOCallbacks:
    def __init__(self):
        pass

    def connectV4(self, peer, host, port):
        pass

    def listenV4(self, peer, host, port):
        pass

    def close(self, peer):
        pass

    def write(self, peer, data, length):
        pass


class PeerManager:
    def __init__(self, stack):
        self.stack = stack
        self.realms = dict()
        self.peers = list()
        self.io_cb = PeerIOCallbacks()

    def clientV4Add(self, host, port):
        peer = Peer(self, PeerStateMachine.PEER_CLIENT)
        return self.io_cb.connectV4(peer, host, port)

    def serverV4Add(self, host, port):
        peer = Peer(self, PeerStateMachine.PEER_LISTEN)
        return self.io_cb.listenV4(peer, host, port)

    def serverV4Accept(self, peer, host, port):
        client_peer = Peer(self, PeerStateMachine.PEER_SERVER)
        return client_peer

    def registerPeerIO(self, pio):
        self.io_cb = pio

    def send(self, peer, message):
        wire = message.getWire()
        self.io_cb.write(peer, wire, len(wire))

    def registerPeer(self, peer, identity, realm, apps):
        peer.identity = identity
        peer.realm = realm
        peer.applications = apps
        if self.realms.has_key(realm):
            prealm = self.realms[realm]
        else:
            prealm = Realm()
            prealm.name = realm
            self.realms[prealm.name] = prealm

        return prealm.addPeer(peer, identity, apps)

    def removerPeer(self, peer):
        if peer.realm in self.realms:
            self.realms[peer.realm].removePeer(peer)