#!/usr/bin/python
# -*- coding: utf-8 -*-
import os
import logging

import wsgiref.handlers
from google.appengine.ext import webapp

from gae_mini_profiler import profiler
from gae_mini_profiler import templatetags

import api_apps
import subtitle
import summer

def main():
    application = webapp.WSGIApplication([
        ('/api-apps/classreport', api_apps.ClassReport),
        ('/api-apps/goalsreport', api_apps.GoalsReport),
        ('/api-apps/goalsreportadmin', api_apps.GoalsReportAdmin),
        ('/api-apps/suggestgoals', api_apps.SuggestGoals),
        ('/api-apps/goals', api_apps.SetGoal),
        ('/api-apps/admin/uploadcommoncore', api_apps.CommonCore),
        ('/api-apps/admin/goalsreport', api_apps.GoalsReportAdmin),
        ('/api-apps/admin/updateexercisedata', api_apps.UpdateExerciseData),
        ('/translations/admin/updatesubtitles', subtitle.UpdateSubtitles),
        ('/translations/subtitlestatus', subtitle.SubtitleStatus),
        ('/translations/subtitleactions', subtitle.SubtitleActions),
        ('/translations/getsubtitlelanguages', subtitle.GetSubtitleLanguages),
        ('/translations/getsubtitlelanguagescount', subtitle.GetSubtitleLanguagesCount),
        ('/summer/application', summer.Application),
        ('/summer/application-status', summer.Status),
        ('/summer/getstudent', summer.GetStudent),
        ('/summer/paypal-autoreturn', summer.PaypalAutoReturn),
        ('/summer/paypal-ipn', summer.PaypalIPN),
        ('/admin/summer/process', summer.Process),
	('/logout', api_apps.Logout),
        (r'.*', api_apps.DefaultHandler),
    ], debug=True)

    application = profiler.ProfilerWSGIMiddleware(application)
    wsgiref.handlers.CGIHandler().run(application)

if __name__ == '__main__':
    main()
