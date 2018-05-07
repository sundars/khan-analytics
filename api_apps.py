import os
import cgi
import urllib2
import urlparse
import simplejson as json
import datetime
import smtplib
import cookie_util
import math
import random
import csv
import StringIO
import re
import logging

from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext import db
from google.appengine.api import mail
from google.appengine.ext import deferred
from google.appengine.runtime import DeadlineExceededError

from oauth_util import OAuthUtil
from class_data import ClassData

class UserGoals(db.Model):
    user = db.StringProperty()
    coaches = db.StringListProperty()
    goals = db.StringListProperty()

class CoachSuggestions(db.Model):
    coach = db.StringProperty()
    list_id = db.StringProperty()
    suggestions = db.StringListProperty()

class CommonCoreMap(db.Model):
    exercise = db.StringProperty()
    grade = db.StringProperty()
    domain = db.StringProperty()
    cc_code = db.StringProperty()
    keywords = db.StringListProperty()

class CommonCoreList(db.Model):
    grades = db.StringListProperty()
    cc_codes = db.StringListProperty()
    domains = db.StringListProperty()

class ExerciseData(db.Model):
    exercise_name = db.StringProperty()
    exercise_display_name = db.StringProperty()
    keywords = db.StringListProperty()

def match_func(matchobj):
    return matchobj.group(1)

def update_common_core_map(cc_file):
    # Delete all Fields in the CommonCoreMap
    query = CommonCoreMap.all()
    cc_map = query.fetch(5000)
    for element in cc_map:
        element.delete()

    grades = []
    domains = []
    cc_codes = []
    cc_file.seek(0)
    reader = csv.reader(cc_file)
    headerline = reader.next()
    for line in reader:
        element = CommonCoreMap()
	element.domain = line[0]
	element.grade = line[2]
	element.cc_code = line[4]
	element.exercise = line[6]
	keywords = element.exercise.lower().split()
	element.keywords = keywords
	for keyword in keywords:
            if re.sub(r"[a-zA-Z]+(s$)",match_func,keyword) == 's':
                element.keywords.append(keyword[0:len(keyword)-1])
	domains.append(element.domain)
	grades.append(element.grade)
	cc_codes.append(element.cc_code)
	element.put()

    # delete the list
    listname = "commoncorelists"
    lists = CommonCoreList.get_by_key_name(listname)
    if lists is None:
        lists = CommonCoreList(key_name=listname)

    keys = {}
    for grade in grades:
        keys[grade] = 1
    lists.grades = keys.keys()

    keys = {}
    for cc_code in cc_codes:
        keys[cc_code] = 1
    lists.cc_codes = keys.keys()

    keys = {}
    for domain in domains:
        keys[domain] = 1
    lists.domains = keys.keys()

    lists.put()
    return

class DefaultHandler(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
	user = json.loads(oauth_util.access_resource("/api/v1/user"))
	user_email = user['email']
	logging.info("Accessing %s; user %s" % (self.request.path, user_email))

	template_values = {
            "username" : user_email,
            "logged_in" : True,
            "logout_url" : "/logout"
        }

	return template_values

    def get(self):
	template_values = {}
	oauth_util = OAuthUtil()
	access_token_key = self.request.cookies.get('access_key')
	token = self.request.get('oauth_token')

	if access_token_key:
	    access_token_secret = self.request.cookies.get('access_secret')
	    oauth_util.set_access_token(access_token_key, access_token_secret)
	    template_values = self.authenticated_response(oauth_util)

        elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
	    oauth_url = oauth_util.get_request_token_url(self.request.url)

            template_values = {
	        "callback" : False,
	        "done" : False,
	        "login_url" : oauth_url
	    }

        self.response.out.write(template.render('default.html', template_values))

class Logout(webapp.RequestHandler):
    def get(self):
	self.response.headers.add_header('Set-Cookie',
	        cookie_util.set_cookie_value("access_key",'',max_age=0))
	self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",'',max_age=0))
	self.redirect('/')

class UpdateExerciseData(webapp.RequestHandler):
    def get(self):
        oauth_util = OAuthUtil()
	logging.info("Accessing %s" % self.request.path)
        exercise_data = json.loads(oauth_util.access_resource("/api/v1/exercises"))

        for exercise in exercise_data:
            query = ExerciseData.all()
            query.filter('exercise_name =', exercise['name'])
            element = query.get()

            if element is None:
                element = ExerciseData()

            element.exercise_name = exercise['name']
            element.exercise_display_name = exercise['display_name']
            keywords = []
            keywords.append(exercise['name'].lower())
            for keyword in exercise['display_name'].lower().split():
                keywords.append(keyword)
                if re.sub(r"[a-zA-Z]+(s$)",match_func,keyword) == 's':
                    keywords.append(keyword[0:len(keyword)-1])
            element.keywords = keywords
            element.put()

        self.response.set_status(200)
        self.response.out.write("OK")
        return

class CommonCore(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
	user = json.loads(oauth_util.access_resource("/api/v1/user?email=coach@khanacademy.org"))
	if user is None or user['email'] != "coach@khanacademy.org":
            self.response.set_status(401)
	    self.response.out.write("Not Authorized")
	    return

	coach_email = json.loads(oauth_util.access_resource("/api/v1/user"))['email']
	logging.info("Accessing %s; user %s" % (self.request.path, coach_email))
	template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
	    "callback" : True,
	    "done" : False,
	    "coach_email" : coach_email,
	    "access_token_key" : oauth_util.access_token.key,
	    "access_token_secret" : oauth_util.access_token.secret
	}

	return template_values

    def post(self):
	cc_file = StringIO.StringIO(self.request.get('commoncore'))
        deferred.defer(update_common_core_map, cc_file)

	coach_email = self.request.get('coach_email')
	template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
	    "done" : True,
	    "coach_email" : coach_email
	}
	self.response.out.write(template.render('uploadfile.html', template_values))
        
    def get(self):
	oauth_util = OAuthUtil()
	token = self.request.get('oauth_token')
	coach_email = self.request.get('coach_email')

        if coach_email:
	    template_values = {
	        "username" : coach_email,
		"logged_in" : True,
		"logout_url" : "/logout",
		"done" : True,
		"coach_email" : coach_email
	    }

        elif token:
	    oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
	        cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
	        template_values = self.authenticated_response(oauth_util)

	    else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

	        template_values = {
	            "callback" : False,
		    "done" : False,
		    "login_url" : oauth_url
                }

	self.response.out.write(template.render('uploadfile.html', template_values))

class SuggestGoals(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
        user = json.loads(oauth_util.access_resource("/api/v1/user"))
	coach_email = user['email']
        coach_key_email = user['key_email']
	logging.info("Accessing %s; user %s" % (self.request.path, coach_email))

	list_all = True
	if self.request.get('common_core'):
	    list_all = False

        list_name = "commoncorelists"
        cc_lists = CommonCoreList.get_by_key_name(list_name)
	if cc_lists is None:
	    list_all = True
	    cc_lists = CommonCoreList(key_name=list_name)

        cc_lists.grades.sort()
        cc_lists.domains.sort()
        cc_lists.cc_codes.sort()

        cc_grade = self.request.get('cc_grade')
        cc_domain = self.request.get('cc_domain')
        cc_keywords = self.request.get('cc_keywords')
        list_id = self.request.get('list_id')
        if not list_id:
            list_id = "allstudents"
	exercises = []
	if not list_all:
            query = CommonCoreMap.all()
	    if len(cc_grade) > 0:
	        query.filter('grade =', cc_grade)
	    if len(cc_domain) > 0:
	        query.filter('domain =', cc_domain)
	    ccmap = query.fetch(1000)
	    for element in ccmap:
		if len(element.exercise) > 0:
		    exercises.append(element.exercise)

            keys = {}
	    for e in exercises:
	        keys[e] = 1
	    exercises = keys.keys()
	else:
	    query = ExerciseData.all()
	    for keyword in cc_keywords.split():
	        query.filter('keywords =', keyword.lower())
	    elements = query.fetch(1000)
	    for element in elements:
	        exercises.append(element.exercise_display_name)

	exercises.sort()

	number_of_exercises = len(exercises)
	bucket_size = float(number_of_exercises)/3

	# Create 3 almost equal lists for display purposes
	all_exercises_1 = []
	begin = 0; end = int(math.ceil(bucket_size))
	for i in range(begin, end):
	    all_exercises_1.append(exercises[i])

	all_exercises_2 = []
	begin = end; end = int(math.floor(float(end) + bucket_size))
	for i in range(begin, end):
	    all_exercises_2.append(exercises[i])

	all_exercises_3 = []
	begin = end; end = number_of_exercises
	for i in range(begin, end):
	    all_exercises_3.append(exercises[i])

        query = CoachSuggestions.all()
	query.filter('coach =', coach_key_email)
	query.filter('list_id =', list_id)
	current_suggestions = query.get()

        remaining_exercises = []
        if current_suggestions is not None:
            for suggestion in current_suggestions.suggestions:
                if suggestion not in exercises:
	            remaining_exercises.append(suggestion)

	remaining_1 = []
	remaining_2 = []
	remaining_3 = []
	remaining= False
        number_of_remaining_exercises = len(remaining_exercises)

	if number_of_remaining_exercises > 0:
            remaining = True
	    bucket_size = float(number_of_remaining_exercises)/3
	    begin = 0; end = int(math.ceil(bucket_size))
	    for i in range(begin, end):
	        remaining_1.append(remaining_exercises[i])

	    begin = end; end = int(math.floor(float(end) + bucket_size))
	    for i in range(begin, end):
	        remaining_2.append(remaining_exercises[i])

	    begin = end; end = number_of_remaining_exercises
	    for i in range(begin, end):
	        remaining_3.append(remaining_exercises[i])

        suggestions = []
        if current_suggestions is not None:
	    suggestions = json.dumps(current_suggestions.suggestions)

        student_list = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/studentlists?email=%s" % coach_email))
        student_list.insert(0, {
            "key" : "allstudents",
            "name" : "All Students"
        })

	template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
	    "callback" : True,
	    "done" : False,
	    "list_all" : list_all,
	    "coach_email" : json.dumps(coach_email),
	    "access_token_key" : json.dumps(oauth_util.access_token.key),
	    "access_token_secret" : json.dumps(oauth_util.access_token.secret),
	    "all_exercises_1" : all_exercises_1,
	    "all_exercises_2" : all_exercises_2,
	    "all_exercises_3" : all_exercises_3,
	    "remaining" : remaining,
	    "remaining_1" : remaining_1,
	    "remaining_2" : remaining_2,
	    "remaining_3" : remaining_3,
	    "current_suggestions" : suggestions,
	    "cc_grades" : cc_lists.grades,
	    "cc_domains" : cc_lists.domains,
	    "cc_codes" : cc_lists.cc_codes,
	    "cc_keywords" : cc_keywords,
	    "filter_grade" : json.dumps(cc_grade),
	    "filter_domain" : json.dumps(cc_domain),
            "list_id" : json.dumps(list_id),
            "student_list" : student_list
	}

	return template_values

    def get(self):
	oauth_util = OAuthUtil()
        token = self.request.get('oauth_token')
        coach_email = self.request.get('coach_email')

	if coach_email:
            list_id = self.request.get('list_id')
            if not list_id:
                list_id = "allstudents"
            display_only = self.request.get('display_only')
	    oauth_util.set_access_token(self.request.get('access_token_key'),
                                        self.request.get('access_token_secret'))
            if not display_only:
	        exercises = json.loads(oauth_util.access_resource("/api/v1/exercises"))
	        suggestions = []
                for exercise in exercises:
		    suggestion = self.request.get(exercise['display_name'])
		    if suggestion is not None:
		        if len(suggestion) > 0:
		            suggestions.append(suggestion)

                coach_key_email = json.loads(oauth_util.access_resource("/api/v1/user?email=%s" % coach_email))['key_email']
                query = CoachSuggestions.all()
                query.filter('coach =', coach_key_email)
                query.filter('list_id =', list_id)
                coach_suggestions = query.get()

	        if coach_suggestions is None:
		    coach_suggestions = CoachSuggestions()
		    coach_suggestions.coach = coach_key_email
                    coach_suggestions.list_id = list_id
		    coach_suggestions.suggestions = suggestions
	        else:
		    coach_suggestions.suggestions = suggestions

	        coach_suggestions.put()
            else:
                coach_key_email = json.loads(oauth_util.access_resource("/api/v1/user?email=%s" % coach_email))['key_email']
                query = CoachSuggestions.all()
                query.filter('coach =', coach_key_email)
                query.filter('list_id =', list_id)
                coach_suggestions = query.get()

                suggestions = []
                if coach_suggestions is not None:
                    suggestions = coach_suggestions.suggestions


            student_list = json.loads(oauth_util.access_resource("/api/v1/user/studentlists?email=%s" % coach_email))
            student_list.insert(0, {
                "key" : "allstudents",
                "name" : "All Students"
            })

	    template_values = {
	        "username" : coach_email,
	        "logged_in" : True,
	        "logout_url" : "/logout",
	        "done" : True,
		"coach_email" : json.dumps(coach_email),
	        "access_token_key" : json.dumps(oauth_util.access_token.key),
	        "access_token_secret" : json.dumps(oauth_util.access_token.secret),
		"coach_suggestions" : suggestions,
                "student_list" : student_list,
                "current_suggestions" : json.dumps(""),
                "filter_grade" : json.dumps(""),
                "filter_domain" : json.dumps(""),
		"list_id" : json.dumps(list_id)
	    }

	elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
	        template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "callback" : False,
		    "done" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('suggestgoals.html', template_values))

class SetGoal(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
        user = json.loads(oauth_util.access_resource("/api/v1/user"))
	student_email = user['email']
	logging.info("Accessing %s; user %s" % (self.request.path, student_email))

        query = UserGoals.all()
	query.filter('user =', student_email)
	user_goals = query.get()

	if user_goals is None:
            user_goals = UserGoals()
	    user_goals.user = student_email
	    user_goals.coaches = user['coaches']
	    user_goals.goals = []
	else:
	    # in the event it has changed between the time we put it in this data store and was
	    # actually updated in Khan Academy
	    user_goals.coaches = user['coaches']

	user_goals.put()

        suggestions = []
        for coach in user['coaches']:
            student_lists = user['student_lists']
            student_lists.append("allstudents")
            for list_id in student_lists:
                query = CoachSuggestions.all()
	        query.filter('coach =', coach)
	        query.filter('list_id =', list_id)
	        coach_suggestions = query.get()

	        if coach_suggestions is not None:
	            suggestions.extend(coach_suggestions.suggestions)

        s = set(suggestions)
	suggestions = list(s)

	exercises = json.loads(oauth_util.access_resource("/api/v1/exercises"))

        # Create some exercise mappings
	all_exercise_names = []
	exercise_display_name_map = {}
	for exercise in exercises:
	    all_exercise_names.append(exercise['name'])
	    exercise_display_name_map[exercise['name']] = exercise['display_name']

        # Remove proficient exercises from all exercises
	exercise_names = []
	if user['all_proficient_exercises'] is not None:
            s = set(user['all_proficient_exercises'])
	    exercise_names = [x for x in all_exercise_names if x not in s]

	# Remove suggsted exercises from all exercises as well
	if user['suggested_exercises'] is not None:
	    s = set(user['suggested_exercises'])
	    t = exercise_names
	    exercise_names = [x for x in t if x not in s]

	# build all_non_proficient_exercises by display name
	all_non_proficient_exercises = []
	for exercise in exercise_names:
            all_non_proficient_exercises.append(exercise_display_name_map[exercise])

        # Remove coach suggested exercises from all_non_proficient_exercises
	if suggestions is not None:
	    s = set(suggestions)
	    t = all_non_proficient_exercises
	    all_non_proficient_exercises = [x for x in t if x not in s]

	# build suggested_exercises by display name
        suggested_exercises = []
	if user['suggested_exercises'] is not None:
	    for exercise in user['suggested_exercises']:
		suggested_exercises.append(exercise_display_name_map[exercise])

        # Remove coach suggested exercises from suggested exercises
	if suggestions is not None:
	    s = set(suggestions)
	    t = suggested_exercises
	    suggested_exercises = [x for x in t if x not in s]

        # build and remove proficient exercises from coach_suggestions
        proficient_coach_suggestions = []
	if user['all_proficient_exercises'] is not None:
	    for exercise in user['all_proficient_exercises']:
                if exercise_display_name_map[exercise] in suggestions:
		    proficient_coach_suggestions.append(exercise_display_name_map[exercise])

        if proficient_coach_suggestions is not None:
            s = set(proficient_coach_suggestions)
	    t = suggestions
	    suggestions = [x for x in t if x not in s]
	    
	suggestions.sort()
	all_non_proficient_exercises.sort()
	suggested_exercises.sort()    

	template_values = {
	    "username" : student_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
	    "callback" : True,
	    "done" : False,
	    "student_email" : student_email,
	    "access_token_key" : oauth_util.access_token.key,
	    "access_token_secret" : oauth_util.access_token.secret,
	    "coach_suggestions" : suggestions,
	    "suggested_exercises" : suggested_exercises,
	    "all_exercises" : all_non_proficient_exercises,
	    "current_goals" : user_goals.goals
	}

	return template_values

    def get(self):
	oauth_util = OAuthUtil()
        token = self.request.get('oauth_token')
        student_email = self.request.get('student_email')

	if student_email:
	    goal_1 = self.request.get('goal_1')
	    goal_2 = self.request.get('goal_2')
	    goal_3 = self.request.get('goal_3')
	    goal_4 = self.request.get('goal_4')
	    goal_5 = self.request.get('goal_5')
	    goal_6 = self.request.get('goal_6')

            # send an email to the student with their goals
	    send_email = self.request.get('send_email')
	    if send_email == '1':
	        fromaddr = 'no-reply@khanacademy.org'
	        toaddr = student_email
	        subject = 'Khan Academy goals'
	        body = "Hello %s,\n\nYou have set the following exercises as your current Khan Academy goals.\n\n%s\n%s\n%s\n%s\n%s\n%s\n\nWe wish you good luck in attaining these goals\n\nCheers!\nKhan Academy Implementation team" % (student_email.split('@')[0],goal_1,goal_2,goal_3,goal_4,goal_5,goal_6)
	        mail.send_mail(sender=fromaddr,
			       to=toaddr,
			       subject=subject,
			       body=body)

	    oauth_util.set_access_token(self.request.get('access_token_key'),
			                self.request.get('access_token_secret'))
	    user = json.loads(oauth_util.access_resource("/api/v1/user"))

            query = UserGoals.all()
	    query.filter('user =', student_email)
	    user_goals = query.get()

            # Update the coaches, just in case it has changed in Khan Academy since when we put
	    # it in our data store
	    user_goals.coaches = user['coaches']
	    user_goals.goals = []
	    if goal_1 is not None:
                user_goals.goals.append(goal_1)
	    if goal_2 is not None:
                user_goals.goals.append(goal_2)
	    if goal_3 is not None:
                user_goals.goals.append(goal_3)
	    if goal_4 is not None:
                user_goals.goals.append(goal_4)
	    if goal_5 is not None:
                user_goals.goals.append(goal_5)
	    if goal_6 is not None:
                user_goals.goals.append(goal_6)

	    user_goals.put()

	    template_values = {
	        "username" : student_email,
	        "logged_in" : True,
	        "logout_url" : "/logout",
	        "done" : True,
		"student_email" : student_email,
		"current_goals" : user_goals.goals
	    }

	elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
	        template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "callback" : False,
		    "done" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('goals.html', template_values))

class GoalsReportAdmin(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
	user = json.loads(oauth_util.access_resource("/api/v1/user"))
        coach_email = user['email']
	coach_key_email = user['key_email']
	logging.info("Accessing %s; user %s" % (self.request.path, coach_email))

        # get the list of students for this coach from Khan Academy (in case this has changed)
        students = json.loads(oauth_util.access_resource("/api/v1/user/students?email=%s" % coach_email))
	student_emails = []
	if students is not None:
	    for student in students:
	        student_emails.append(student['email'])
	     		 
        # look into our data store and remove students who no longer have this user as their coach
        query = UserGoals.all()
        query.filter('coaches =', coach_key_email)
        user_goals = query.fetch(1000)

	for user_goal in user_goals:
	    if user_goal.user not in student_emails:
                user_goal.coaches.remove(coach_key_email)
		user_goal.put()

        # redo the query to get the new list of students
        query = UserGoals.all()
        query.filter('coaches =', coach_key_email)
        user_goals = query.fetch(1000)

        class_goals = {
            "coach_email" : coach_email,
            "goals" : user_goals
        }

        template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
            "callback" : True,
            "done" : False,
            "coach_email" : coach_email,
            "access_token_key" : oauth_util.access_token.key,
            "access_token_secret" : oauth_util.access_token.secret,
            "class_goals" : class_goals
        }

	return template_values

    def get(self):
	oauth_util = OAuthUtil()
        token = self.request.get('oauth_token')
        proxy_coach_email = self.request.get('proxy_coach_email')

        if proxy_coach_email:
	    oauth_util.set_access_token(self.request.get('access_token_key'),
			                self.request.get('access_token_secret'))
	    user = json.loads(oauth_util.access_resource("/api/v1/user?email=%s" % (proxy_coach_email)))
	    if user is None or user['email'] != proxy_coach_email:
		self.response.set_status(401)
                self.response.out.write('Not Authorized')
		return

            proxy_coach_key_email = user['key_email']
            query = UserGoals.all()
	    query.filter('coaches =', proxy_coach_key_email)
	    user_goals = query.fetch(1000)

            class_goals_report = "Student,Goal 1,Goal 2,Goal 3,Goal 4,Goal 5,Goal 6\n"
	    for goals in user_goals:
                class_goals_report += goals.user
		for goal in goals.goals:
		    class_goals_report += ",%s" % goal
		class_goals_report += "\n"

            self.response.headers['Content-Type'] = "text/csv"
	    self.response.headers['Content-Disposition'] = "attachment; filename=class_goals.csv"

	    self.response.out.write(class_goals_report)

	elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

            self.response.out.write(template.render('goalsreportadmin.html', template_values))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
	        template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "callback" : False,
		    "done" : False,
	            "login_url" : oauth_url
	        }

            self.response.out.write(template.render('goalsreportadmin.html', template_values))

class GoalsReport(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
        user = json.loads(oauth_util.access_resource("/api/v1/user"))
	coach_email = user['email']
	coach_key_email = user['key_email']
	logging.info("Accessing %s; user %s" % (self.request.path, coach_email))

        # get the list of students for this coach from Khan Academy (in case this has changed)
        list_id = self.request.get('list_id')
        if not list_id or list_id == "allstudents":
            students = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/students?email=%s" % coach_email))
            list_id = "allstudents"
        else:
            students = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/students?email=%s&list_id=%s" %
                                  (coach_email, list_id)))

	student_emails = []
	if students is not None:
	    for student in students:
	        student_emails.append(student['email'])
        
        if not list_id or list_id == "allstudents":
            # look into our data store and remove students who no longer have this user as their coach
            query = UserGoals.all()
            query.filter('coaches =', coach_key_email)
            user_goals = query.fetch(1000)

	    for user_goal in user_goals:
	        if user_goal.user not in student_emails:
                    user_goal.coaches.remove(coach_key_email)
		    user_goal.put()

            # redo the query to get the new list of students
            query = UserGoals.all()
            query.filter('coaches =', coach_key_email)
            user_goals = query.fetch(1000)
        else:
            query = UserGoals.all()
            query.filter('coaches =', coach_key_email)
            query.filter('user IN', student_emails)
            user_goals = query.fetch(1000)

        class_goals = {
            "coach_email" : coach_email,
            "goals" : user_goals
        }

        student_list = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/studentlists?email=%s" % coach_email))
        student_list.insert(0, {
            "key" : "allstudents",
            "name" : "All Students"
        })

        template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
            "callback" : True,
            "done" : False,
            "coach_email" : coach_email,
            "access_token_key" : json.dumps(oauth_util.access_token.key),
            "access_token_secret" : json.dumps(oauth_util.access_token.secret),
            "class_goals" : class_goals,
            "student_list" : student_list,
            "list_id" : json.dumps(list_id)
        }

	return template_values

    def get(self):
	oauth_util = OAuthUtil()
        token = self.request.get('oauth_token')
        coach_email = self.request.get('coach_email')

        if coach_email:
	    oauth_util.set_access_token(self.request.get('access_token_key'),
			                self.request.get('access_token_secret'))
	    user = json.loads(oauth_util.access_resource("/api/v1/user"))
	    coach_key_email = user['key_email']

            list_id = self.request.get('list_id')
	    student_emails = []
            if list_id and list_id != "allstudents":
                students = json.loads(oauth_util.access_resource(
                                      "/api/v1/user/students?email=%s&list_id=%s" %
                                      (coach_email, list_id)))

	        if students is not None:
	            for student in students:
	                student_emails.append(student['email'])

            query = UserGoals.all()
	    query.filter('coaches =', coach_key_email)
            if list_id and list_id != "allstudents":
                query.filter('user IN', student_emails)
	    user_goals = query.fetch(1000)

            class_goals_report = "Student,Goal 1,Goal 2,Goal 3,Goal 4,Goal 5,Goal 6\n"
	    for goals in user_goals:
                class_goals_report += goals.user
		for goal in goals.goals:
		    class_goals_report += ",%s" % goal
		class_goals_report += "\n"

            self.response.headers['Content-Type'] = "text/csv"
	    self.response.headers['Content-Disposition'] = "attachment; filename=class_goals.csv"

	    self.response.out.write(class_goals_report)

	elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

            self.response.out.write(template.render('goalsreport.html', template_values))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
	        template_values = self.authenticated_response(oauth_util)

            else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "callback" : False,
		    "done" : False,
	            "login_url" : oauth_url
	        }

            self.response.out.write(template.render('goalsreport.html', template_values))

class ClassReport(webapp.RequestHandler):
    def authenticated_response(self, oauth_util):
	coach_email = json.loads(oauth_util.access_resource("/api/v1/user"))['email']
	logging.info("Accessing %s; user %s" % (self.request.path, coach_email))

        list_id = self.request.get('list_id')
        student_list = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/studentlists?email=%s" % coach_email))
        student_list.insert(0, {
            "key" : "allstudents",
            "name" : "All Students"
        })

	template_values = {
	    "username" : coach_email,
	    "logged_in" : True,
	    "logout_url" : "/logout",
	    "callback" : True,
	    "done" : False,
	    "coach_email" : coach_email,
	    "access_token_key" : json.dumps(oauth_util.access_token.key),
	    "access_token_secret" : json.dumps(oauth_util.access_token.secret),
            "student_list" : student_list,
            "list_id" : json.dumps(list_id)
	}

	return template_values

    def get(self):
	oauth_util = OAuthUtil()
        token = self.request.get('oauth_token')
        coach_email = self.request.get('coach_email')

        if coach_email:
	    oauth_util.set_access_token(self.request.get('access_token_key'),
			                self.request.get('access_token_secret'))
            class_data = ClassData()
	    class_data.coach = coach_email
	    class_data.mailto = coach_email
	    class_data.unique_key = ''.join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for x in range(10))

            activity_log = self.request.get('activity_log')
	    if activity_log == '1':
                class_data.activity_log = True

            goals_data = self.request.get('goals_data')
            if goals_data == '1':
                class_data.generate_goals_dashboard = True

                user = json.loads(oauth_util.access_resource("/api/v1/user?email=%s" % coach_email))
		if user is None or user['email'] != coach_email:
                    self.response.set_status(401)
	            self.response.out.write("Not Authorized")
	            return

                coach_suggested_goals = self.request.get('coach_suggested_goals')
		coach_key_email = user['key_email']
                if coach_suggested_goals == '1':
                    list_id = self.request.get('list_id')
                    if not list_id or list_id == "allstudents":
                        students = json.loads(oauth_util.access_resource(
                                              "/api/v1/user/students?email=%s" % coach_email))
                        list_id = "allstudents"
                    else:
                        students = json.loads(oauth_util.access_resource(
                                              "/api/v1/user/students?email=%s&list_id=%s" %
                                              (coach_email, list_id)))

                    query = CoachSuggestions.all()
	            query.filter('coach =', coach_key_email)
	            query.filter('list_id =', list_id)
	            coach_suggestions = query.get()

                    class_data.student_goals = "Student,"
                    for i in range(0,len(coach_suggestions.suggestions)):
                        class_data.student_goals += "Goal %d," % (i+1)
                    class_data.student_goals += "\n"

                    for student in students:
                        class_data.student_goals += student['email']
                        if len(coach_suggestions.suggestions) > 0:
                            for suggestion in coach_suggestions.suggestions:
                                class_data.student_goals += ",%s" % suggestion
                            class_data.student_goals += "\n"
                else:
                    query = UserGoals.all()
	            query.filter('coaches =', coach_key_email)
	            user_goals = query.fetch(1000)

                    class_data.student_goals = "Student,Goal 1,Goal 2,Goal 3,Goal 4,Goal 5,Goal 6\n"
	            for goals in user_goals:
                        class_data.student_goals += goals.user
		        for goal in goals.goals:
		            class_data.student_goals += ",%s" % goal
		        class_data.student_goals += "\n"

            proxy_coach = self.request.get('proxy_coach')
	    if proxy_coach:
                class_data.mailto = proxy_coach
		coach_email = proxy_coach

	    class_data.tz_offset_mins = int(self.request.get('tz_offset'))
	    class_data.access_token = oauth_util.access_token
            class_data.consumer_key = oauth_util.consumer_key
            class_data.consumer_secret = oauth_util.consumer_secret
	    class_data.server_url = oauth_util.server_url
            class_data.list_id = self.request.get('list_id')

            class_data.run()

	    template_values = {
	        "username" : coach_email,
	        "logged_in" : True,
	        "logout_url" : "/logout",
		"done" : True,
	        "coach_email" : coach_email,
	    }

	elif token:
            oauth_util.get_access_token(self)
	    template_values = self.authenticated_response(oauth_util)
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_key",oauth_util.access_token.key,max_age=2629743))
	    self.response.headers.add_header('Set-Cookie',
		cookie_util.set_cookie_value("access_secret",oauth_util.access_token.secret,max_age=2629743))

	else:
	    access_token_key = self.request.cookies.get('access_key')
	    access_token_secret = self.request.cookies.get('access_secret')
	    if access_token_key:
	        oauth_util.set_access_token(access_token_key, access_token_secret)
		template_values = self.authenticated_response(oauth_util)

	    else:
	        oauth_url = oauth_util.get_request_token_url(self.request.url)

                template_values = {
	            "callback" : False,
		    "done" : False,
	            "login_url" : oauth_url
	        }

        self.response.out.write(template.render('classreport.html', template_values))
