import logging
from google.appengine.api import users
import os

# Customize should_profile to return true whenever a request should be profiled.
# This function will be run once per request, so make sure its contents are fast.
class ProfilerConfigProduction:
    @staticmethod
    def should_profile(environ):
        return len(os.environ["HTTP_HOST"].split(".")) >= 4

class ProfilerConfigDevelopment:
    @staticmethod
    def should_profile(environ):
        return True
