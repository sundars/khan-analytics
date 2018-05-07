import os, logging
import cgi
import logging
import urllib2
import urllib
import urlparse
import simplejson as json
import datetime
import smtplib
import cookie_util
import math
import random
from google.appengine.api import urlfetch

from oauth_provider.oauth import OAuthConsumer, OAuthToken, OAuthRequest, OAuthSignatureMethod_HMAC_SHA1

class OAuthUtil:
    consumer_key = "42gcLVunVUkvQd4H"
    consumer_secret = "DqNZpKy58Vea32yE"
    server_url = "http://www.khanacademy.org"
    usub_url = "http://www.universalsubtitles.org"
    youtube_url = "http://www.youtube.com"
    access_token = None

    def get_response(self, url):
        response = ""
        file = None
        try:
            file = urllib2.urlopen(url)
            response = file.read()
        finally:
            if file:
                file.close()

        return response

    def get_result(self, rpc):
	result = rpc.get_result()
	if result.status_code == 200:
            return result.content.strip()

    def access_usub_resource(self, relative_url, youtube_id, authenticate):
        data = {}
        if authenticate:
            data['username'] = 'Translations'
            data['password'] = 'translations'
        data['video_url'] = "%s/watch?v=%s" % (self.youtube_url, youtube_id)
        url_values = urllib.urlencode(data)
        base_url = "%s%s?" % (self.usub_url, relative_url)
        full_url = base_url + url_values

        response = self.get_response(full_url)
        return response.strip()

    def access_resource(self, relative_url, async=False):
        full_url = self.server_url + relative_url
        url = urlparse.urlparse(full_url)
        query_params = cgi.parse_qs(url.query)
        for key in query_params:
                query_params[key] = query_params[key][0]

        oauth_consumer = OAuthConsumer(self.consumer_key, self.consumer_secret)
        oauth_request = OAuthRequest.from_consumer_and_token(
                oauth_consumer,
                token = self.access_token,
                http_url = full_url,
                parameters = query_params
	        )

	oauth_request.sign_request(OAuthSignatureMethod_HMAC_SHA1(), oauth_consumer, self.access_token)

	if not async:
	    response = self.get_response(oauth_request.to_url())
            return response.strip()
        else:
	    rpc = urlfetch.create_rpc()
	    urlfetch.make_fetch_call(rpc, oauth_request.to_url())
	    return rpc

    def set_access_token(self, key, secret):
        self.access_token = OAuthToken(key,secret)

    def get_access_token(self, handler):
        token = handler.request.get('oauth_token')
        secret = handler.request.get('oauth_token_secret')
        verifier = handler.request.get('oauth_verifier')

        request_token = OAuthToken(token, secret)

        oauth_consumer = OAuthConsumer(self.consumer_key, self.consumer_secret)
        oauth_request = OAuthRequest.from_consumer_and_token(
                                oauth_consumer,
                                token = request_token,
                                verifier = verifier,
                                http_url = "%s/api/auth/access_token" % self.server_url
                                )

        oauth_request.sign_request(OAuthSignatureMethod_HMAC_SHA1(), oauth_consumer, request_token)
        response = self.get_response(oauth_request.to_url())
        self.access_token = OAuthToken.from_string(response)


    def get_request_token_url(self, callback):
	oauth_consumer = OAuthConsumer(self.consumer_key, self.consumer_secret)
	oauth_request = OAuthRequest.from_consumer_and_token(
	                oauth_consumer,
	                callback = callback,
	                http_url = "%s/api/auth/request_token" % self.server_url
	                )

        oauth_request.sign_request(OAuthSignatureMethod_HMAC_SHA1(), oauth_consumer, None)
        oauth_url = oauth_request.to_url()
	return oauth_url
