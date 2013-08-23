import sys, os
import socket
import ssl
import select
import time, datetime
import traceback
import modules

class ServerDisconnectedException(Exception):
	pass

class Sockets(object):

	def __init__(self):
		self.management = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.management.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.management.setblocking(0)
	
		self.irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.irc = ssl.wrap_socket(self.irc)
	
		self.inputs = {self.irc: None, self.management: None}
		self.outputs = []
		self.errors = self.inputs


class Client(object):

	def __init__(self, config):
		self.config = config
		self.sendq = []
		self.recvq = []
		self.termop = "\r\n"
		self.verbose = True
		self.delay = False
		self.closing = False
		self.connected = False
		self.timeout = 300
		self.version = 3.0
		self.env = sys.platform

	def connect(self, server, port):
		socket.setdefaulttimeout(self.timeout)
		self.sockets = Sockets()
		try:
			self.sockets.management.bind(('', 10000))
			self._log("dbg", "Listening on management port (%s)..." % str(self.sockets.management.getsockname()))
			self.sockets.management.listen(0)
		except Exception:
			pass
		self._log("dbg", "Connecting to Freenode (%s:%s)..." % (server, port))
		self.sockets.irc.connect((server, port))
		self.sockets.irc.setblocking(0)
		self.connected = True

		self._log("dbg", "Starting main loop...")
		self._loop()
		
	def _loop(self):
		p = Parser(self, self.config)
		while True:
			self._select()

			if self.closing:
				break
			
			if len(self.sendq) > 0:
				self._send(self.sockets.irc)
			
			if self.recvq:
				lines = ''.join(self.recvq).split(self.termop)
				del self.recvq[:]
				if lines[-1]:
					self.recvq.append(lines[-1])
					del lines[-1]
				n = 1
				for n, line in enumerate(lines, 1):
					if line:
						self._log('dbg', 'Parsing %s' % repr(line))
						p.interpret(line)
					else:
						n -= 1
				if n > 1:
					self._log('dbg', 'Parsed %d sentence%s.%s' % (n, '' if n == 1 else 's', ' More pending.' if self.recvq else ''))
			
	def _select(self):
		self._log("dbg", "Waiting for select()...")
		ready_read, ready_write, in_error = select.select(self.sockets.inputs, self.sockets.outputs, self.sockets.errors, self.timeout)
		self._log("dbg", "select() returned %d read, %d write, %d error" % (len(ready_read), len(ready_write), len(in_error)))
		
		for sock in ready_read:
			if sock is self.sockets.management:
				conn, addr = self.sockets.management.accept()
				conn.setblocking(0)
				self._log("dbg", "New management connection from %s:%d" % (addr[0], addr[1]))
				self.sockets.inputs[conn] = addr
			else:
				self._recv(sock, 1500)
		
		for sock in ready_write:
			self.sockets.outputs.remove(sock)
		
		for sock in in_error:
			self._log("dbg", "select() caught socket #%d in exceptional condition" % sock.fileno())
			del self.sockets.errors[conn]

		if not ready_read and not ready_write and not in_error:
			self._log("dbg", "select() timed out")
			self._shutdown()

	def _shutdown(self):
		self._log("dbg", "Shutting down IRC socket...")
		self.sockets.irc.shutdown(socket.SHUT_RDWR)
		#self.sockets.irc = self.sockets.irc.unwrap()
		#self.sockets.irc.shutdown(socket.SHUT_RDWR)
		self.sockets.irc.close()
		#self.closing = True
		self.connected = False
		raise ServerDisconnectedException
	
	def disconnect(self, n, frame):
		if self.connected:
			self._sendq(['QUIT'], "See ya~")
			self._send(self.sockets.irc)
		self.connected = False
		sys.exit()
		
	def _recv(self, sock, bytes):
		try:
			data = sock.recv(bytes)
		except ssl.SSLError as e:
			if e.errno == ssl.SSL_ERROR_WANT_READ:
				self._log("dbg", "Couldn't read: SSL_ERROR_WANT_READ, re-running select()")
				self._log("dbg", "recvq contains %s" % self.recvq)
				return
		if data:
			if self.verbose:
				self._log('in', data)
				self.recvq.append(data)
		else:
			if sock in self.sockets.inputs and sock is not None:
				self._log("dbg", "Closed connection from %s:%d" % (self.sockets.inputs[sock][0], self.sockets.inputs[sock][1]))
				del self.sockets.inputs[sock]
				sock.close()

	def _sendq(self, left, right = None):
		if right:
			limit = 445
			for line in right.splitlines(): # don't use self.termop as \r and \n are both treated as newlines in the IRC protocol, otherwise this is exploitable
				if line:
					lines = [line[i:i+limit] for i in range(0, len(line), limit)]
					for n in range(len(lines)):
						self.sendq.append("%s :%s%s" % (' '.join(left), lines[n], self.termop))
		else:
			self.sendq.append("%s%s" % (' '.join(left), self.termop))
		if self.sockets.irc not in self.sockets.outputs:
			self.sockets.outputs.append(self.sockets.irc)

	def _send(self, sock):
		lines = len(self.sendq)
		burst = 5
		delay = 2
		for i in range(0, lines, delay):
			if not self.delay:
				buffer = ''.join(self.sendq[:burst])
				del self.sendq[:burst]
				self.delay = True
			else:
				buffer = ''.join(self.sendq[:delay])
				del self.sendq[:delay]
				time.sleep(delay)
			if self.verbose:
				self._log('out', buffer)
			sock.write(buffer)
			break
		
		if len(self.sendq) > 0:
			self._log('dbg', 'There are still %d bytes queued to be sent.' % sum(len(q) for q in self.sendq))
			self.sockets.outputs.append(self.sockets.irc)
		else:
			self.delay = False

	def _log(self, flow, buffer):
		log = datetime.datetime.now().strftime("%b %d %Y %H:%M:%S")
		buffer = buffer.replace(self.config.get(self.config.active_network, 'password'), '***********')
		if flow == "out":
			_pad = "<<<"
			self._log('dbg', 'Sending %d bytes' % len(buffer))
		elif flow == "in":
			_pad = ">>>"
			self._log('dbg', 'Received %d bytes' % len(buffer))
		elif flow == "dbg":
			_pad = "DBG"
			buffer += self.termop
		for index, line in enumerate(buffer.split(self.termop)):
			if line:
				if index == 0:
					pad = _pad
				else:
					pad = "   "
				
				output = "%s %s %s" % (log, pad, line.encode('string_escape').replace("\\'", "'").replace("\\\\", "\\"))
				print output
				for conn in self.sockets.inputs:
					if conn != self.sockets.irc:
						try:
							conn.send(output + self.termop)
						except IOError:
							pass


class Parser(object):
	def __init__(self, bot, config):
		self.bot = bot
		self.config = config
		#super(Parser, self).__init__(config)
		self.network = config.active_network
		self.init = {
			'ident': 0, 'retries': 0, 'ready': False, 'log': True,
			'registered': True if config.has_option(self.network, 'password') else False,
			'identified': False, 'joined': False
		}
		self.inv = {
			'rooms': {},
			'banned': []
		}
		self.remote = {}
		self.previous = {}
		self.voice = True
		self.name = config.get(self.network, 'nick')
		self.admin = config.get(self.network, 'admin')

	def interpret(self, line):
		self.remote['server'] = None
		self.remote['nick'] = None
		self.remote['user'] = None
		self.remote['host'] = None
		self.remote['receiver'] = None
		self.remote['misc'] = None
		self.remote['message'] = None
		
		try:
			if line.startswith(':'):
				if ' :' in line:
					args, trailing = line[1:].split(' :', 1)
					args = args.split()
				else:
					args = line[1:].split()
					trailing = args[-1]
				
				if "@" in args[0]:
					client = args[0].split("@")
					self.remote['nick'] = client[0].split("!")[0]
					self.remote['user'] = client[0].split("!")[1]
					self.remote['host'] = client[1]
				else:
					self.remote['server'] = args[0]
				
				self.remote['mid'] = args[1]
				self.remote['message'] = trailing
				
				try:
					self.remote['receiver'] = args[2]
				except IndexError:
					pass
				try:
					self.remote['misc'] = args[3:]
				except IndexError:
					pass
				self._init()

				if self.init['ident'] and self.remote['mid'] in ['376', '422']:
					self.init['ready'] = True
					
				if self.init['ready']:
					if self.remote['message']:
						self.remote['sendee'] = self.remote['receiver'] if self.remote['receiver'] != self.nick else self.remote['nick']
						try:
							if self.init['log'] and self.init['joined'] and self.remote['mid'] == "PRIVMSG":
								modules.logger.log(self, self.remote['sendee'], self.remote['nick'], self.remote['message'])
							modules.io.read(self)
						except:
							error_message = "Traceback (most recent call last):\n" + '\n'.join(traceback.format_exc().split("\n")[-4:-1])
							self.bot._sendq(("NOTICE", self.remote['sendee'] or self.admin), error_message)
					if self.init['joined']:
						self._updateNicks()				
			else:
				arg	= line.split(" :")[0]
				message = line.split(" :", 1)[1]
				self._init()

				if arg == "PING":
					self.bot._sendq(['PONG'], message)
		except Exception, e:
			self.bot._log("dbg", "Error parsing input: %s (%s)" % (repr(line), e))

	def _sendq(self, left, right = None):
		if self.init['log'] and self.init['joined'] and left[0] == "PRIVMSG":
			if self.remote['receiver'] == self.nick: self.remote['receiver'] = self.remote['nick']
			if type(right) != str: raise AssertionError("send queue must be <type 'str'> but was found as %s" % type(right))
			modules.logger.log(self, left[1], self.nick, right)
		self.bot._sendq(left, right)
	
	def _init(self):
		if self.remote['message'] and self.init['ident'] is not True:
			if self.remote['message']:
				self.init['ident'] += 1
		if self.init['ident'] > 1:
			while not self.init['retries'] or self.remote['mid'] in ['433', '437']:
				self._ident()
				break
			if self.remote['mid'] == "001":
				self.init['ident'] = True

	def _ident(self):
		self.nick = self.name # + "_" * self.init['retries']
		self.bot._sendq(("NICK", self.nick))
		self.bot._sendq(("USER", self.nick, self.nick, self.nick), self.nick)
		self.init['retries'] += 1
	
	def _login(self):
		self.bot._sendq(("PRIVMSG", "NickServ"), "IDENTIFY %s" % self.config.get(self.network, 'password'))
	
	def _updateNicks(self):
		if self.remote['mid'] == "JOIN":
			if self.remote['nick'] == self.nick:
				self.inv['rooms'][self.remote['message']] = {}
			else:
				self.inv['rooms'][self.remote['message']][self.remote['nick']] = {}
		elif self.remote['mid'] == "353":
			for user in self.remote['message'].split():
				self.inv['rooms'][self.remote['misc'][1]][user.lstrip("~.@%+")] = {}
				if __import__('re').search('^[~\.@%\+]', user):
					if user[0] in ['~', '.']:
						mode = 'q'
					elif user[0] == '@':
						mode = 'o'
					elif user[0] == '%':
						mode = 'h'
					elif user[0] == '+':
						mode = 'v'
					self.inv['rooms'][self.remote['misc'][1]][user[1:]]['mode'] = mode or None
				else:
					self.inv['rooms'][self.remote['misc'][1]][user]['mode'] = None
		elif self.remote['mid'] == "PART":
				if self.remote['nick'] == self.nick:
					del self.inv['rooms'][self.remote['receiver']]
				else:
					del self.inv['rooms'][self.remote['receiver']][self.remote['nick']]
		elif self.remote['mid'] == "KICK":
			if self.remote['misc'][0].lower() != self.nick.lower():
				del self.inv['rooms'][self.remote['receiver']][self.remote['misc'][0]]
			else:
				del self.inv['rooms'][self.remote['receiver']]
		elif self.remote['mid'] == "NICK":
			for room in self.inv['rooms']:
				if self.remote['nick'] in self.inv['rooms'][room]:
					self.inv['rooms'][room][self.remote['message']] = self.inv['rooms'][room][self.remote['nick']]
					del self.inv['rooms'][room][self.remote['nick']]
			if self.remote['nick'].lower() in self.inv['banned']:
				self.inv['banned'][self.inv['banned'].index(self.remote['nick'].lower())] = self.remote['message'].lower()
		elif self.remote['mid'] == "QUIT":
			for room in self.inv['rooms']:
				if self.remote['nick'] in self.inv['rooms'][room]:
					del self.inv['rooms'][room][self.remote['nick']]
		elif self.remote['mid'] == "MODE":
			if len(self.remote['misc']) == 2:
				if self.remote['misc'][0].startswith("+") and self.remote['misc'][0][1] in ['o', 'h', 'v']:
					self.inv['rooms'][self.remote['receiver']][self.remote['misc'][1]]['mode'] = self.remote['misc'][0][1]
				elif self.remote['misc'][0].startswith("-") and self.remote['misc'][0][1] in ['o', 'h', 'v']:
					self.inv['rooms'][self.remote['receiver']][self.remote['misc'][1]]['mode'] = None

	def _reload(self, args):
		if len(args) == 1:
			reload(modules)
			response = "Success: Reloaded all submodules."
		elif len(args) == 2:
			if os.path.exists("%s/modules/%s.py" % (os.path.dirname(__file__), args[1])):
				reload(__import__('modules.' + args[1], globals(), locals(), fromlist = [], level = 1))
				response = "Success: Reloaded '%s' submodule." % args[1]
			else:
				response = "Failure: No such module '%s'." % args[1]
		elif len(args) > 2:
			affected, unaffected = [], []
			for module in args[1:]:
				if os.path.exists("%s/modules/%s.py" % (os.path.dirname(__file__), module)):
					reload(__import__('modules.' + module, globals(), locals(), fromlist = [], level = 1))
					affected.append(module)
				else:
					unaffected.append(module)
			if (len(args[1:]) - len(unaffected)) == len(args[1:]):
				response = "Success: Reloaded %s submodules." % ', '.join(args[1:])
			elif len(unaffected) < len(args[1:]):
				pl1 = "" if len(unaffected) == 1 else "s"
				pl2 = "was" if len(affected) == 1 else "were"
				response = "Partial: Could not reload %s submodule%s but %s %s ok." % (', '.join(unaffected), pl1, ', '.join(affected), pl2)
			else:
				response = "Failure: No such modules."
			del affected, unaffected
		
		self._sendq(("PRIVMSG", self.remote['sendee']), response)
