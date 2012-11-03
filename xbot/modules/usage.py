import sys
import urllib, urllib2
import cookielib
import lxml.html

def usage(bot, args):
	if len(args) == 1:
		session = cookielib.CookieJar()
		opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(session))
		opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:11.0) Gecko/20100101 Firefox/11.0')]

		form = lxml.html.fromstring(opener.open('https://secure.2degreesmobile.co.nz/web/ip/login').read()).xpath("//form[@name='loginFrm']")
		if not form:
			return "2degrees: Error, cannot find login form."
			sys.exit()

		account = opener.open(form[0].get('action'), urllib.urlencode(
			{
				'userid': bot.config.get('module: usage', 'login'),
				'password': bot.config.get('module: usage', 'pass'),
				'hdnAction': 'login',
				'hdnAuthenticationType': 'M'
			}
		)).read()
		remaining = lxml.html.fromstring(account).xpath("//td[@class='tableBillamount']/text()")

		if not remaining:
			return "2degrees: Error, cannot get remaining data."
			sys.exit()
		
		orcon = lxml.html.fromstring(opener.open('http://www.orcon.net.nz/modules/usagemeter/view/CosmosController.php').read()).xpath('//dd[last()]/text()')
		if not orcon:
			return "Orcon: Error, cannot fetch details."
		
		return "3G: %s remaining\nADSL: %s used" % (', '.join(remaining).encode('utf-8'), orcon[0].encode('utf-8'))
	else:
		return "Usage: !%s" % args[0]
