# -*- test-case-name: twisted.internet.test.test_endpoints -*-
# Copyright (c) Twisted Matrix Laboratories.
# See LICENSE for details.
"""
Implementations of L{IStreamServerEndpoint} and L{IStreamClientEndpoint} that
wrap the L{IReactorTCP}, L{IReactorSSL}, and L{IReactorUNIX} interfaces.

This also implements an extensible mini-language for describing endpoints,
parsed by the L{clientFromString} and L{serverFromString} functions.

@since: 10.1
"""

import os, socket

from zope.interface import implements
import warnings

from twisted.internet import interfaces, defer, error, fdesc
from twisted.plugin import IPlugin, getPlugins
from twisted.internet.interfaces import IStreamServerEndpointStringParser
from twisted.internet.interfaces import IStreamClientEndpointStringParser
from twisted.python.filepath import FilePath
from twisted.python.systemd import ListenFDs
from twisted.internet import stdio
from twisted.internet.stdio import PipeAddress

from twisted.internet._endpointspy3 import (
    _WrappingFactory, TCP4ServerEndpoint, TCP6ServerEndpoint,
    TCP4ClientEndpoint, TCP6ClientEndpoint, SSL4ClientEndpoint,
    SSL4ServerEndpoint)

__all__ = ["clientFromString", "serverFromString",
           "TCP4ServerEndpoint", "TCP6ServerEndpoint",
           "TCP4ClientEndpoint", "TCP6ClientEndpoint",
           "UNIXServerEndpoint", "UNIXClientEndpoint",
           "SSL4ServerEndpoint", "SSL4ClientEndpoint",
           "AdoptedStreamServerEndpoint", "StandardIOEndpoint"]



class StandardIOEndpoint(object):
    """
    A Standard Input/Output endpoint
    """
    implements(interfaces.IStreamServerEndpoint)

    def __init__(self, reactor):
        """
        @param reactor: The reactor for the endpoint
        """
        self._reactor = reactor


    def listen(self, stdioProtocolFactory):
        """
        Implement L{IStreamServerEndpoint.listen} to listen on stdin/stdout
        """
        return defer.execute(stdio.StandardIO,
                             stdioProtocolFactory.buildProtocol(PipeAddress()))



class UNIXServerEndpoint(object):
    """
    UnixSocket server endpoint.
    """
    implements(interfaces.IStreamServerEndpoint)

    def __init__(self, reactor, address, backlog=50, mode=0666, wantPID=0):
        """
        @param reactor: An L{IReactorUNIX} provider.
        @param address: The path to the Unix socket file, used when listening
        @param backlog: number of connections to allow in backlog.
        @param mode: mode to set on the unix socket.  This parameter is
            deprecated.  Permissions should be set on the directory which
            contains the UNIX socket.
        @param wantPID: If True, create a pidfile for the socket.
        """
        self._reactor = reactor
        self._address = address
        self._backlog = backlog
        self._mode = mode
        self._wantPID = wantPID


    def listen(self, protocolFactory):
        """
        Implement L{IStreamServerEndpoint.listen} to listen on a UNIX socket.
        """
        return defer.execute(self._reactor.listenUNIX, self._address,
                             protocolFactory,
                             backlog=self._backlog,
                             mode=self._mode,
                             wantPID=self._wantPID)



class UNIXClientEndpoint(object):
    """
    UnixSocket client endpoint.
    """
    implements(interfaces.IStreamClientEndpoint)

    def __init__(self, reactor, path, timeout=30, checkPID=0):
        """
        @param reactor: An L{IReactorUNIX} provider.

        @param path: The path to the Unix socket file, used when connecting
        @type path: str

        @param timeout: Number of seconds to wait before assuming the
            connection has failed.
        @type timeout: int

        @param checkPID: If True, check for a pid file to verify that a server
            is listening.
        @type checkPID: bool
        """
        self._reactor = reactor
        self._path = path
        self._timeout = timeout
        self._checkPID = checkPID


    def connect(self, protocolFactory):
        """
        Implement L{IStreamClientEndpoint.connect} to connect via a
        UNIX Socket
        """
        try:
            wf = _WrappingFactory(protocolFactory)
            self._reactor.connectUNIX(
                self._path, wf,
                timeout=self._timeout,
                checkPID=self._checkPID)
            return wf._onConnection
        except:
            return defer.fail()



class AdoptedStreamServerEndpoint(object):
    """
    An endpoint for listening on a file descriptor initialized outside of
    Twisted.

    @ivar _used: A C{bool} indicating whether this endpoint has been used to
        listen with a factory yet.  C{True} if so.
    """
    implements(interfaces.IStreamServerEndpoint)

    _close = os.close
    _setNonBlocking = staticmethod(fdesc.setNonBlocking)

    def __init__(self, reactor, fileno, addressFamily):
        """
        @param reactor: An L{IReactorSocket} provider.

        @param fileno: An integer file descriptor corresponding to a listening
            I{SOCK_STREAM} socket.

        @param addressFamily: The address family of the socket given by
            C{fileno}.
        """
        self.reactor = reactor
        self.fileno = fileno
        self.addressFamily = addressFamily
        self._used = False


    def listen(self, factory):
        """
        Implement L{IStreamServerEndpoint.listen} to start listening on, and
        then close, C{self._fileno}.
        """
        if self._used:
            return defer.fail(error.AlreadyListened())
        self._used = True

        try:
            self._setNonBlocking(self.fileno)
            port = self.reactor.adoptStreamPort(
                self.fileno, self.addressFamily, factory)
            self._close(self.fileno)
        except:
            return defer.fail()
        return defer.succeed(port)



def _parseTCP(factory, port, interface="", backlog=50):
    """
    Internal parser function for L{_parseServer} to convert the string
    arguments for a TCP(IPv4) stream endpoint into the structured arguments.

    @param factory: the protocol factory being parsed, or C{None}.  (This was a
        leftover argument from when this code was in C{strports}, and is now
        mostly None and unused.)

    @type factory: L{IProtocolFactory} or C{NoneType}

    @param port: the integer port number to bind
    @type port: C{str}

    @param interface: the interface IP to listen on
    @param backlog: the length of the listen queue
    @type backlog: C{str}

    @return: a 2-tuple of (args, kwargs), describing  the parameters to
        L{IReactorTCP.listenTCP} (or, modulo argument 2, the factory, arguments
        to L{TCP4ServerEndpoint}.
    """
    return (int(port), factory), {'interface': interface,
                                  'backlog': int(backlog)}



def _parseUNIX(factory, address, mode='666', backlog=50, lockfile=True):
    """
    Internal parser function for L{_parseServer} to convert the string
    arguments for a UNIX (AF_UNIX/SOCK_STREAM) stream endpoint into the
    structured arguments.

    @param factory: the protocol factory being parsed, or C{None}.  (This was a
        leftover argument from when this code was in C{strports}, and is now
        mostly None and unused.)

    @type factory: L{IProtocolFactory} or C{NoneType}

    @param address: the pathname of the unix socket
    @type address: C{str}

    @param backlog: the length of the listen queue
    @type backlog: C{str}

    @param lockfile: A string '0' or '1', mapping to True and False
        respectively.  See the C{wantPID} argument to C{listenUNIX}

    @return: a 2-tuple of (args, kwargs), describing  the parameters to
        L{IReactorTCP.listenUNIX} (or, modulo argument 2, the factory,
        arguments to L{UNIXServerEndpoint}.
    """
    return (
        (address, factory),
        {'mode': int(mode, 8), 'backlog': int(backlog),
         'wantPID': bool(int(lockfile))})



def _parseSSL(factory, port, privateKey="server.pem", certKey=None,
              sslmethod=None, interface='', backlog=50):
    """
    Internal parser function for L{_parseServer} to convert the string
    arguments for an SSL (over TCP/IPv4) stream endpoint into the structured
    arguments.

    @param factory: the protocol factory being parsed, or C{None}.  (This was a
        leftover argument from when this code was in C{strports}, and is now
        mostly None and unused.)
    @type factory: L{IProtocolFactory} or C{NoneType}

    @param port: the integer port number to bind
    @type port: C{str}

    @param interface: the interface IP to listen on
    @param backlog: the length of the listen queue
    @type backlog: C{str}

    @param privateKey: The file name of a PEM format private key file.
    @type privateKey: C{str}

    @param certKey: The file name of a PEM format certificate file.
    @type certKey: C{str}

    @param sslmethod: The string name of an SSL method, based on the name of a
        constant in C{OpenSSL.SSL}.  Must be one of: "SSLv23_METHOD",
        "SSLv2_METHOD", "SSLv3_METHOD", "TLSv1_METHOD".
    @type sslmethod: C{str}

    @return: a 2-tuple of (args, kwargs), describing  the parameters to
        L{IReactorSSL.listenSSL} (or, modulo argument 2, the factory, arguments
        to L{SSL4ServerEndpoint}.
    """
    from twisted.internet import ssl
    if certKey is None:
        certKey = privateKey
    kw = {}
    if sslmethod is not None:
        kw['sslmethod'] = getattr(ssl.SSL, sslmethod)
    cf = ssl.DefaultOpenSSLContextFactory(privateKey, certKey, **kw)
    return ((int(port), factory, cf),
            {'interface': interface, 'backlog': int(backlog)})



class _StandardIOParser(object):
    """
    Stream server endpoint string parser for the Standard I/O type.

    @ivar prefix: See L{IStreamClientEndpointStringParser.prefix}.
    """
    implements(IPlugin, IStreamServerEndpointStringParser)

    prefix = "stdio"

    def _parseServer(self, reactor):
        """
        Internal parser function for L{_parseServer} to convert the string
        arguments into structured arguments for the L{StandardIOEndpoint}

        @param reactor: Reactor for the endpoint
        """
        return StandardIOEndpoint(reactor)


    def parseStreamServer(self, reactor, *args, **kwargs):
        # Redirects to another function (self._parseServer), tricks zope.interface
        # into believing the interface is correctly implemented.
        return self._parseServer(reactor)



class _SystemdParser(object):
    """
    Stream server endpoint string parser for the I{systemd} endpoint type.

    @ivar prefix: See L{IStreamClientEndpointStringParser.prefix}.

    @ivar _sddaemon: A L{ListenFDs} instance used to translate an index into an
        actual file descriptor.
    """
    implements(IPlugin, IStreamServerEndpointStringParser)

    _sddaemon = ListenFDs.fromEnvironment()

    prefix = "systemd"

    def _parseServer(self, reactor, domain, index):
        """
        Internal parser function for L{_parseServer} to convert the string
        arguments for a systemd server endpoint into structured arguments for
        L{AdoptedStreamServerEndpoint}.

        @param reactor: An L{IReactorSocket} provider.

        @param domain: The domain (or address family) of the socket inherited
            from systemd.  This is a string like C{"INET"} or C{"UNIX"}, ie the
            name of an address family from the L{socket} module, without the
            C{"AF_"} prefix.
        @type domain: C{str}

        @param index: An offset into the list of file descriptors inherited from
            systemd.
        @type index: C{str}

        @return: A two-tuple of parsed positional arguments and parsed keyword
            arguments (a tuple and a dictionary).  These can be used to
            construct an L{AdoptedStreamServerEndpoint}.
        """
        index = int(index)
        fileno = self._sddaemon.inheritedDescriptors()[index]
        addressFamily = getattr(socket, 'AF_' + domain)
        return AdoptedStreamServerEndpoint(reactor, fileno, addressFamily)


    def parseStreamServer(self, reactor, *args, **kwargs):
        # Delegate to another function with a sane signature.  This function has
        # an insane signature to trick zope.interface into believing the
        # interface is correctly implemented.
        return self._parseServer(reactor, *args, **kwargs)



class _TCP6ServerParser(object):
    """
    Stream server endpoint string parser for the TCP6ServerEndpoint type.

    @ivar prefix: See L{IStreamClientEndpointStringParser.prefix}.
    """
    implements(IPlugin, IStreamServerEndpointStringParser)

    prefix = "tcp6"     # Used in _parseServer to identify the plugin with the endpoint type

    def _parseServer(self, reactor, port, backlog=50, interface='::'):
        """
        Internal parser function for L{_parseServer} to convert the string
        arguments into structured arguments for the L{TCP6ServerEndpoint}

        @param reactor: An L{IReactorTCP} provider.

        @param port: The port number used for listening
        @type port: int

        @param backlog: Size of the listen queue
        @type backlog: int

        @param interface: The hostname to bind to
        @type interface: str
        """
        port = int(port)
        backlog = int(backlog)
        return TCP6ServerEndpoint(reactor, port, backlog, interface)


    def parseStreamServer(self, reactor, *args, **kwargs):
        # Redirects to another function (self._parseServer), tricks zope.interface
        # into believing the interface is correctly implemented.
        return self._parseServer(reactor, *args, **kwargs)



_serverParsers = {"tcp": _parseTCP,
                  "unix": _parseUNIX,
                  "ssl": _parseSSL,
                  }

_OP, _STRING = range(2)

def _tokenize(description):
    """
    Tokenize a strports string and yield each token.

    @param description: a string as described by L{serverFromString} or
        L{clientFromString}.

    @return: an iterable of 2-tuples of (L{_OP} or L{_STRING}, string).  Tuples
        starting with L{_OP} will contain a second element of either ':' (i.e.
        'next parameter') or '=' (i.e. 'assign parameter value').  For example,
        the string 'hello:greet\=ing=world' would result in a generator
        yielding these values::

            _STRING, 'hello'
            _OP, ':'
            _STRING, 'greet=ing'
            _OP, '='
            _STRING, 'world'
    """
    current = ''
    ops = ':='
    nextOps = {':': ':=', '=': ':'}
    description = iter(description)
    for n in description:
        if n in ops:
            yield _STRING, current
            yield _OP, n
            current = ''
            ops = nextOps[n]
        elif n == '\\':
            current += description.next()
        else:
            current += n
    yield _STRING, current



def _parse(description):
    """
    Convert a description string into a list of positional and keyword
    parameters, using logic vaguely like what Python does.

    @param description: a string as described by L{serverFromString} or
        L{clientFromString}.

    @return: a 2-tuple of C{(args, kwargs)}, where 'args' is a list of all
        ':'-separated C{str}s not containing an '=' and 'kwargs' is a map of
        all C{str}s which do contain an '='.  For example, the result of
        C{_parse('a:b:d=1:c')} would be C{(['a', 'b', 'c'], {'d': '1'})}.
    """
    args, kw = [], {}
    def add(sofar):
        if len(sofar) == 1:
            args.append(sofar[0])
        else:
            kw[sofar[0]] = sofar[1]
    sofar = ()
    for (type, value) in _tokenize(description):
        if type is _STRING:
            sofar += (value,)
        elif value == ':':
            add(sofar)
            sofar = ()
    add(sofar)
    return args, kw


# Mappings from description "names" to endpoint constructors.
_endpointServerFactories = {
    'TCP': TCP4ServerEndpoint,
    'SSL': SSL4ServerEndpoint,
    'UNIX': UNIXServerEndpoint,
    }

_endpointClientFactories = {
    'TCP': TCP4ClientEndpoint,
    'SSL': SSL4ClientEndpoint,
    'UNIX': UNIXClientEndpoint,
    }


_NO_DEFAULT = object()

def _parseServer(description, factory, default=None):
    """
    Parse a stports description into a 2-tuple of arguments and keyword values.

    @param description: A description in the format explained by
        L{serverFromString}.
    @type description: C{str}

    @param factory: A 'factory' argument; this is left-over from
        twisted.application.strports, it's not really used.
    @type factory: L{IProtocolFactory} or L{None}

    @param default: Deprecated argument, specifying the default parser mode to
        use for unqualified description strings (those which do not have a ':'
        and prefix).
    @type default: C{str} or C{NoneType}

    @return: a 3-tuple of (plugin or name, arguments, keyword arguments)
    """
    args, kw = _parse(description)
    if not args or (len(args) == 1 and not kw):
        deprecationMessage = (
            "Unqualified strport description passed to 'service'."
            "Use qualified endpoint descriptions; for example, 'tcp:%s'."
            % (description,))
        if default is None:
            default = 'tcp'
            warnings.warn(
                deprecationMessage, category=DeprecationWarning, stacklevel=4)
        elif default is _NO_DEFAULT:
            raise ValueError(deprecationMessage)
        # If the default has been otherwise specified, the user has already
        # been warned.
        args[0:0] = [default]
    endpointType = args[0]
    parser = _serverParsers.get(endpointType)
    if parser is None:
        # If the required parser is not found in _server, check if
        # a plugin exists for the endpointType
        for plugin in getPlugins(IStreamServerEndpointStringParser):
            if plugin.prefix == endpointType:
                return (plugin, args[1:], kw)
        raise ValueError("Unknown endpoint type: '%s'" % (endpointType,))
    return (endpointType.upper(),) + parser(factory, *args[1:], **kw)



def _serverFromStringLegacy(reactor, description, default):
    """
    Underlying implementation of L{serverFromString} which avoids exposing the
    deprecated 'default' argument to anything but L{strports.service}.
    """
    nameOrPlugin, args, kw = _parseServer(description, None, default)
    if type(nameOrPlugin) is not str:
        plugin = nameOrPlugin
        return plugin.parseStreamServer(reactor, *args, **kw)
    else:
        name = nameOrPlugin
    # Chop out the factory.
    args = args[:1] + args[2:]
    return _endpointServerFactories[name](reactor, *args, **kw)



def serverFromString(reactor, description):
    """
    Construct a stream server endpoint from an endpoint description string.

    The format for server endpoint descriptions is a simple string.  It is a
    prefix naming the type of endpoint, then a colon, then the arguments for
    that endpoint.

    For example, you can call it like this to create an endpoint that will
    listen on TCP port 80::

        serverFromString(reactor, "tcp:80")

    Additional arguments may be specified as keywords, separated with colons.
    For example, you can specify the interface for a TCP server endpoint to
    bind to like this::

        serverFromString(reactor, "tcp:80:interface=127.0.0.1")

    SSL server endpoints may be specified with the 'ssl' prefix, and the
    private key and certificate files may be specified by the C{privateKey} and
    C{certKey} arguments::

        serverFromString(reactor, "ssl:443:privateKey=key.pem:certKey=crt.pem")

    If a private key file name (C{privateKey}) isn't provided, a "server.pem"
    file is assumed to exist which contains the private key. If the certificate
    file name (C{certKey}) isn't provided, the private key file is assumed to
    contain the certificate as well.

    You may escape colons in arguments with a backslash, which you will need to
    use if you want to specify a full pathname argument on Windows::

        serverFromString(reactor,
            "ssl:443:privateKey=C\\:/key.pem:certKey=C\\:/cert.pem")

    finally, the 'unix' prefix may be used to specify a filesystem UNIX socket,
    optionally with a 'mode' argument to specify the mode of the socket file
    created by C{listen}::

        serverFromString(reactor, "unix:/var/run/finger")
        serverFromString(reactor, "unix:/var/run/finger:mode=660")

    This function is also extensible; new endpoint types may be registered as
    L{IStreamServerEndpointStringParser} plugins.  See that interface for more
    information.

    @param reactor: The server endpoint will be constructed with this reactor.

    @param description: The strports description to parse.

    @return: A new endpoint which can be used to listen with the parameters
        given by by C{description}.

    @rtype: L{IStreamServerEndpoint<twisted.internet.interfaces.IStreamServerEndpoint>}

    @raise ValueError: when the 'description' string cannot be parsed.

    @since: 10.2
    """
    return _serverFromStringLegacy(reactor, description, _NO_DEFAULT)



def quoteStringArgument(argument):
    """
    Quote an argument to L{serverFromString} and L{clientFromString}.  Since
    arguments are separated with colons and colons are escaped with
    backslashes, some care is necessary if, for example, you have a pathname,
    you may be tempted to interpolate into a string like this::

        serverFromString("ssl:443:privateKey=%s" % (myPathName,))

    This may appear to work, but will have portability issues (Windows
    pathnames, for example).  Usually you should just construct the appropriate
    endpoint type rather than interpolating strings, which in this case would
    be L{SSL4ServerEndpoint}.  There are some use-cases where you may need to
    generate such a string, though; for example, a tool to manipulate a
    configuration file which has strports descriptions in it.  To be correct in
    those cases, do this instead::

        serverFromString("ssl:443:privateKey=%s" %
                         (quoteStringArgument(myPathName),))

    @param argument: The part of the endpoint description string you want to
        pass through.

    @type argument: C{str}

    @return: The quoted argument.

    @rtype: C{str}
    """
    return argument.replace('\\', '\\\\').replace(':', '\\:')



def _parseClientTCP(*args, **kwargs):
    """
    Perform any argument value coercion necessary for TCP client parameters.

    Valid positional arguments to this function are host and port.

    Valid keyword arguments to this function are all L{IReactorTCP.connectTCP}
    arguments.

    @return: The coerced values as a C{dict}.
    """

    if len(args) == 2:
        kwargs['port'] = int(args[1])
        kwargs['host'] = args[0]
    elif len(args) == 1:
        if 'host' in kwargs:
            kwargs['port'] = int(args[0])
        else:
            kwargs['host'] = args[0]

    try:
        kwargs['port'] = int(kwargs['port'])
    except KeyError:
        pass

    try:
        kwargs['timeout'] = int(kwargs['timeout'])
    except KeyError:
        pass
    return kwargs



def _loadCAsFromDir(directoryPath):
    """
    Load certificate-authority certificate objects in a given directory.

    @param directoryPath: a L{FilePath} pointing at a directory to load .pem
        files from.

    @return: a C{list} of L{OpenSSL.crypto.X509} objects.
    """
    from twisted.internet import ssl

    caCerts = {}
    for child in directoryPath.children():
        if not child.basename().split('.')[-1].lower() == 'pem':
            continue
        try:
            data = child.getContent()
        except IOError:
            # Permission denied, corrupt disk, we don't care.
            continue
        try:
            theCert = ssl.Certificate.loadPEM(data)
        except ssl.SSL.Error:
            # Duplicate certificate, invalid certificate, etc.  We don't care.
            pass
        else:
            caCerts[theCert.digest()] = theCert.original
    return caCerts.values()



def _parseClientSSL(*args, **kwargs):
    """
    Perform any argument value coercion necessary for SSL client parameters.

    Valid keyword arguments to this function are all L{IReactorSSL.connectSSL}
    arguments except for C{contextFactory}.  Instead, C{certKey} (the path name
    of the certificate file) C{privateKey} (the path name of the private key
    associated with the certificate) are accepted and used to construct a
    context factory.

    Valid positional arguments to this function are host and port.

    @param caCertsDir: The one parameter which is not part of
        L{IReactorSSL.connectSSL}'s signature, this is a path name used to
        construct a list of certificate authority certificates.  The directory
        will be scanned for files ending in C{.pem}, all of which will be
        considered valid certificate authorities for this connection.

    @type caCertsDir: C{str}

    @return: The coerced values as a C{dict}.
    """
    from twisted.internet import ssl
    kwargs = _parseClientTCP(*args, **kwargs)
    certKey = kwargs.pop('certKey', None)
    privateKey = kwargs.pop('privateKey', None)
    caCertsDir = kwargs.pop('caCertsDir', None)
    if certKey is not None:
        certx509 = ssl.Certificate.loadPEM(
            FilePath(certKey).getContent()).original
    else:
        certx509 = None
    if privateKey is not None:
        privateKey = ssl.PrivateCertificate.loadPEM(
            FilePath(privateKey).getContent()).privateKey.original
    else:
        privateKey = None
    if caCertsDir is not None:
        verify = True
        caCerts = _loadCAsFromDir(FilePath(caCertsDir))
    else:
        verify = False
        caCerts = None
    kwargs['sslContextFactory'] = ssl.CertificateOptions(
        method=ssl.SSL.SSLv23_METHOD,
        certificate=certx509,
        privateKey=privateKey,
        verify=verify,
        caCerts=caCerts
    )
    return kwargs



def _parseClientUNIX(*args, **kwargs):
    """
    Perform any argument value coercion necessary for UNIX client parameters.

    Valid keyword arguments to this function are all L{IReactorUNIX.connectUNIX}
    keyword arguments except for C{checkPID}.  Instead, C{lockfile} is accepted
    and has the same meaning.  Also C{path} is used instead of C{address}.

    Valid positional arguments to this function are C{path}.

    @return: The coerced values as a C{dict}.
    """
    if len(args) == 1:
        kwargs['path'] = args[0]

    try:
        kwargs['checkPID'] = bool(int(kwargs.pop('lockfile')))
    except KeyError:
        pass
    try:
        kwargs['timeout'] = int(kwargs['timeout'])
    except KeyError:
        pass
    return kwargs

_clientParsers = {
    'TCP': _parseClientTCP,
    'SSL': _parseClientSSL,
    'UNIX': _parseClientUNIX,
    }



def clientFromString(reactor, description):
    """
    Construct a client endpoint from a description string.

    Client description strings are much like server description strings,
    although they take all of their arguments as keywords, aside from host and
    port.

    You can create a TCP client endpoint with the 'host' and 'port' arguments,
    like so::

        clientFromString(reactor, "tcp:host=www.example.com:port=80")

    or, without specifying host and port keywords::

        clientFromString(reactor, "tcp:www.example.com:80")

    Or you can specify only one or the other, as in the following 2 examples::

        clientFromString(reactor, "tcp:host=www.example.com:80")
        clientFromString(reactor, "tcp:www.example.com:port=80")

    or an SSL client endpoint with those arguments, plus the arguments used by
    the server SSL, for a client certificate::

        clientFromString(reactor, "ssl:web.example.com:443:"
                                  "privateKey=foo.pem:certKey=foo.pem")

    to specify your certificate trust roots, you can identify a directory with
    PEM files in it with the C{caCertsDir} argument::

        clientFromString(reactor, "ssl:host=web.example.com:port=443:"
                                  "caCertsDir=/etc/ssl/certs")

    You can create a UNIX client endpoint with the 'path' argument and optional
    'lockfile' and 'timeout' arguments::

        clientFromString(reactor, "unix:path=/var/foo/bar:lockfile=1:timeout=9")

    or, with the path as a positional argument with or without optional
    arguments as in the following 2 examples::

        clientFromString(reactor, "unix:/var/foo/bar")
        clientFromString(reactor, "unix:/var/foo/bar:lockfile=1:timeout=9")

    This function is also extensible; new endpoint types may be registered as
    L{IStreamClientEndpointStringParser} plugins.  See that interface for more
    information.

    @param reactor: The client endpoint will be constructed with this reactor.

    @param description: The strports description to parse.

    @return: A new endpoint which can be used to connect with the parameters
        given by by C{description}.
    @rtype: L{IStreamClientEndpoint<twisted.internet.interfaces.IStreamClientEndpoint>}

    @since: 10.2
    """
    args, kwargs = _parse(description)
    aname = args.pop(0)
    name = aname.upper()
    for plugin in getPlugins(IStreamClientEndpointStringParser):
        if plugin.prefix.upper() == name:
            return plugin.parseStreamClient(*args, **kwargs)
    if name not in _clientParsers:
        raise ValueError("Unknown endpoint type: %r" % (aname,))
    kwargs = _clientParsers[name](*args, **kwargs)
    return _endpointClientFactories[name](reactor, **kwargs)
