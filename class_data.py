from __future__ import with_statement
import cgi
import logging
import urllib2
import urlparse
import webbrowser
import simplejson as json
import datetime
import smtplib
import os, logging
import StringIO
import random
import time

from optparse import OptionParser
from oauth_provider.oauth import OAuthConsumer, OAuthToken, OAuthRequest, OAuthSignatureMethod_HMAC_SHA1
from google.appengine.ext import db
from google.appengine.ext import blobstore
from google.appengine.ext.blobstore import BlobInfo
from google.appengine.api import mail
from google.appengine.api import files
from google.appengine.ext import deferred
from google.appengine import runtime

from oauth_util import OAuthUtil

from pyExcelerator import *
import csv
import tarfile
from types import *

NUM_OF_SHARDS = 50

class StudentData(db.Model):
    student_email = db.StringProperty()
    coaches = db.StringListProperty()
    member_for = db.IntegerProperty(default=0)
    energy_points = db.IntegerProperty(default=0)
    exercises_proficient = db.IntegerProperty(default=0)
    videos_watched = db.IntegerProperty(default=0)
    meteorite_badges = db.IntegerProperty(default=0)
    moon_badges = db.IntegerProperty(default=0)
    earth_badges = db.IntegerProperty(default=0)
    sun_badges = db.IntegerProperty(default=0)
    blackhole_badges = db.IntegerProperty(default=0)
    challenge_patches = db.IntegerProperty(default=0)
    ea_ic_1 = db.IntegerProperty(default=0)
    ea_oc_1 = db.IntegerProperty(default=0)
    ea_1 = db.IntegerProperty(default=0)
    va_ic_1 = db.IntegerProperty(default=0)
    va_oc_1 = db.IntegerProperty(default=0)
    va_1 = db.IntegerProperty(default=0)
    ea_ic_7 = db.IntegerProperty(default=0)
    ea_oc_7 = db.IntegerProperty(default=0)
    ea_7 = db.IntegerProperty(default=0)
    va_ic_7 = db.IntegerProperty(default=0)
    va_oc_7 = db.IntegerProperty(default=0)
    va_7 = db.IntegerProperty(default=0)
    ea_ic_30 = db.IntegerProperty(default=0)
    ea_oc_30 = db.IntegerProperty(default=0)
    ea_30 = db.IntegerProperty(default=0)
    va_ic_30 = db.IntegerProperty(default=0)
    va_oc_30 = db.IntegerProperty(default=0)
    va_30 = db.IntegerProperty(default=0)
    ea_ic = db.IntegerProperty(default=0)
    ea_oc = db.IntegerProperty(default=0)
    ea = db.IntegerProperty(default=0)
    va_ic = db.IntegerProperty(default=0)
    va_oc = db.IntegerProperty(default=0)
    va = db.IntegerProperty(default=0)
    num_retries = db.IntegerProperty(default=0)

class StudentExerciseData(db.Model):
    student_email = db.StringProperty()
    name = db.StringProperty()
    display_name = db.StringProperty()
    ekey = db.StringProperty()
    status = db.StringProperty()
    is_proficient = db.IntegerProperty(default=0)
    number_of_problems_attempted = db.IntegerProperty(default=0)
    number_correct = db.IntegerProperty(default=0)
    current_streak = db.IntegerProperty(default=0)
    longest_streak = db.IntegerProperty(default=0)
    days_since_proficient = db.IntegerProperty(default=0)
    days_since_last_attempted = db.IntegerProperty(default=0)

class SavedState(db.Model):
    coach_key = db.StringProperty()
    total_number_of_students = db.IntegerProperty(required=True, default=0)

class CounterShard(db.Model):
    number_of_completed_students = db.IntegerProperty(required=True, default=0)

def increment_completed(ckey):
    index = random.randint(0, NUM_OF_SHARDS - 1)
    shardname = ckey + str(index)
    counter = CounterShard.get_by_key_name(shardname)
    if counter is None:
	counter = CounterShard(key_name=shardname)
    counter.number_of_completed_students += 1
    counter.put()

def send_email_to_coach(class_data):
    # No need for mutex
    coach_key = "%s-%s" % (class_data.coach, class_data.unique_key)
    query = SavedState.all()
    query.filter('coach_key =', coach_key)
    state = query.get()

    number_of_completed_students = 0
    for i in range(NUM_OF_SHARDS):
        shardname = coach_key + str(i)
	counter = CounterShard.get_by_key_name(shardname)
	if counter is not None:
	    number_of_completed_students += counter.number_of_completed_students

    # if not done, enqueue back into the task queue and return
    if number_of_completed_students != state.total_number_of_students:
	time.sleep(7)
	deferred.defer(send_email_to_coach, class_data)
	return

    # else, all students' data have been written, time to send email
    try:
        class_data.send_email_to_coach()
    except Exception, e:
        logging.error("send_email_to_coach failed with error: %s" % e)

    # clean-up and exit
    for i in range(NUM_OF_SHARDS):
        shardname = coach_key + str(i)
	counter = CounterShard.get_by_key_name(shardname)
	if counter is not None:
	    counter.delete()

    state.delete()
    return

def write_student_data_worker(student, coach, unique_key, access_token, activity_log, tz_offset_mins):
    try:
        write_student_data(student, coach, access_token, activity_log, tz_offset_mins)

        # Needs to be protected by a mutex, run transactionally
        coach_key = "%s-%s" % (coach, unique_key)
	db.run_in_transaction(increment_completed, coach_key)

    except runtime.DeadlineExceededError:
        logging.error("write_student_data_worker for student %s: DeadlineExceededError" % student['email'])
	deferred.defer(write_student_data_worker,
		       student, coach,
		       unique_key,
		       access_token,
		       activity_log,
		       tz_offset_mins)
	return
    except Exception, e:
        logging.error("write_student_data_worker for student %s: failed with error <%s>" % (student['email'], e))
	deferred.defer(write_student_data_worker,
		       student, coach,
		       unique_key,
		       access_token,
		       activity_log,
		       tz_offset_mins)
	return

    return

def class_data_worker(class_data):
    oauth_util = OAuthUtil()
    oauth_util.access_token = class_data.access_token

    # Remove this coach from the list of all students in our datastore, the coach will be added later on
    query = StudentData.all()
    query.filter('coaches =', class_data.coach)
    students = query.fetch(1000)
    for student in students:
	student.coaches.remove(class_data.coach)
	student.put()

    student_list = None
    get_students = True
    while get_students:
        try:
            if len(class_data.list_id) == 0 or class_data.list_id == "allstudents":
                student_list = json.loads(oauth_util.access_resource(
                                          "/api/v1/user/students?email=%s" %
                                          class_data.coach))
            else:
                student_list = json.loads(oauth_util.access_resource(
                                          "/api/v1/user/students?email=%s&list_id=%s" %
                                          (class_data.coach, class_data.list_id)))
	    get_students = False
        except Exception, e:
	    logging.error("/api/v1/user/students failed with error: %s" % e)

    # No need for mutex
    coach_key = "%s-%s" % (class_data.coach, class_data.unique_key)
    query = SavedState.all()
    query.filter('coach_key =', coach_key)
    state = query.get()
    state.total_number_of_students = len(student_list)
    state.put()

    try:
        for student in student_list:
	    deferred.defer(write_student_data_worker,
			   student,
			   class_data.coach,
			   class_data.unique_key,
			   class_data.access_token,
			   class_data.activity_log,
			   class_data.tz_offset_mins)

    except runtime.DeadlineExceededError:
        logging.error("class_data_worker: DeadlineExceededError")
        return
    except Exception, e:
        logging.error("class_data_worker failed with error: %s" % e)
        return

    deferred.defer(send_email_to_coach, class_data)
    return

def write_student_data(user_data, coach, access_token, activity_log, tz_offset_mins):
    oauth_util = OAuthUtil()
    oauth_util.access_token = access_token

    # Get data
    student_email = user_data['email']
    query = StudentData.all()
    query.filter('student_email =', student_email)
    student_data = query.get()

    if student_data == None:
        student_data = StudentData()
	student_data.coaches = user_data['coaches']
	student_data.student_email = student_email
        student_data.num_retries = 0

    if student_data.num_retries > 5:
        student_data.num_retries = 0
        student_data.put()
        return

    student_data.num_retries += 1
    student_data.put()

    # reset the coaches in case they have changed
    student_data.coaches = user_data['coaches']
    student_data.coaches.append(coach)
    keys = {}
    for coach in student_data.coaches:
	keys[coach] = 1
    student_data.coaches = keys.keys()

    dt_joined_utc = datetime.datetime.strptime(user_data['joined'], "%Y-%m-%dT%H:%M:%SZ")
    dt_joined_ctz = dt_joined_utc + datetime.timedelta(minutes=tz_offset_mins)
    dt_now_ctz = datetime.datetime.now()
    timedelta = dt_now_ctz - dt_joined_ctz
    student_data.member_for = timedelta.days
    student_data.energy_points = user_data['points']
    student_data.exercises_proficient = len(user_data['all_proficient_exercises'])

    student_data.meteorite_badges = user_data['badge_counts']['0']
    student_data.moon_badges = user_data['badge_counts']['1']
    student_data.earth_badges = user_data['badge_counts']['2']
    student_data.sun_badges = user_data['badge_counts']['3']
    student_data.blackhole_badges = user_data['badge_counts']['4']
    student_data.challenge_patches = user_data['badge_counts']['5']

    student_data.videos_watched = 0
    if activity_log:
        video_activity_in_class = {}
        video_activity_outside_class = {}

    video_data = json.loads(oauth_util.access_resource("/api/v1/user/videos?email=%s" % student_email))
    if video_data is not None:
        for video in video_data:
	    if video['completed'] == True:
	        student_data.videos_watched += 1

            if activity_log:
                youtube_id = video['video']['youtube_id']
                video_log_data = json.loads(oauth_util.access_resource("/api/v1/user/videos/%s/log?email=%s" %
		                                                       (youtube_id, student_email)))

		if video_log_data is not None:
                    for video_log in video_log_data:
	                dt_video_log_utc = datetime.datetime.strptime(video_log['time_watched'],
		                                                      "%Y-%m-%dT%H:%M:%SZ")
	                dt_video_log_ctz = dt_video_log_utc + datetime.timedelta(minutes=tz_offset_mins)
	                timedelta = dt_now_ctz - dt_video_log_ctz
	                if dt_video_log_ctz.hour > 8 and dt_video_log_ctz.hour < 16:
		            if timedelta.days in video_activity_in_class:
	                        video_activity_in_class[timedelta.days] += video_log['seconds_watched']
		            else:
	                        video_activity_in_class[timedelta.days] = video_log['seconds_watched']
	                else:
		            if timedelta.days in video_activity_outside_class:
	                        video_activity_outside_class[timedelta.days] += video_log['seconds_watched']
		            else:
	                        video_activity_outside_class[timedelta.days] = video_log['seconds_watched']

        if activity_log:
            # today
            student_data.va_ic_1 = 0
            student_data.va_oc_1 = 0
            if 0 in video_activity_in_class:
                student_data.va_ic_1 += video_activity_in_class[0]
            if 0 in video_activity_outside_class:
                student_data.va_oc_1 += video_activity_outside_class[0]
	    student_data.va_1 = student_data.va_ic_1+student_data.va_oc_1

            # last 7 days
            student_data.va_ic_7 = student_data.va_ic_1
            student_data.va_oc_7 = student_data.va_oc_1
            for i in range(1, 6):
                if i in video_activity_in_class:
                    student_data.va_ic_7 += video_activity_in_class[i]
                if i in video_activity_outside_class:
                    student_data.va_oc_7 += video_activity_outside_class[i]
	    student_data.va_7 = student_data.va_ic_7+student_data.va_oc_7

            # last 30 days
            student_data.va_ic_30 = student_data.va_ic_7
            student_data.va_oc_30 = student_data.va_oc_7
            for i in range(7, 30):
                if i in video_activity_in_class:
                    student_data.va_ic_30 += video_activity_in_class[i]
                if i in video_activity_outside_class:
                    student_data.va_oc_30 += video_activity_outside_class[i]
	    student_data.va_30 = student_data.va_ic_30+student_data.va_oc_30

            # total
            student_data.va_ic = student_data.va_ic_30
            student_data.va_oc = student_data.va_oc_30
            for i in range(31, len(video_activity_in_class)):
                if i in video_activity_in_class:
                    student_data.va_ic += video_activity_in_class[i]
                if i in video_activity_outside_class:
                    student_data.va_oc += video_activity_outside_class[i]
	    student_data.va = student_data.va_ic+student_data.va_oc

    if activity_log:
        exercise_activity_in_class = {}
        exercise_activity_outside_class = {}

    topics = json.loads(oauth_util.access_resource("/api/v1/exercise_topics"))
    if topics is not None:
        for topic in topics:
            exercises = json.loads(oauth_util.access_resource("/api/v1/user/topic/%s/exercises?email=%s" % (topic['id'], student_email)))
            if exercises is not None:
                for exercise in exercises:
                    if exercise['total_done'] == 0 and not exercise['exercise_states']['proficient']:
	                continue

                    query = StudentExerciseData.all()
	            query.filter('student_email =', student_data.student_email)
                    query.filter('name =', exercise['exercise'])
	            exercise_data = query.get()

                    if exercise_data is None:
	                exercise_data = StudentExerciseData()
	                exercise_data.student_email = student_data.student_email
	                exercise_data.name = exercise['exercise']
                        exercise_data.display_name = exercise['exercise_model']['display_name']
	                exercise_data.ekey = exercise_data.student_email + exercise_data.display_name

                    exercise_data.number_of_problems_attempted = exercise['total_done']
	            exercise_data.number_correct = exercise['total_correct']
	            exercise_data.current_streak = exercise['streak']
	            exercise_data.longest_streak = exercise['longest_streak']

                    exercise_data.days_since_proficient = -1
                    exercise_data.is_proficient = 0
	            if exercise['proficient_date'] != None and (type(exercise['proficient_date']) is StringType or type(exercise['proficient_date']) is UnicodeType):
	                dt_utc = datetime.datetime.strptime(exercise['proficient_date'], "%Y-%m-%dT%H:%M:%SZ")
                        dt_ctz = dt_utc + datetime.timedelta(minutes=tz_offset_mins)
                        timedelta = dt_now_ctz - dt_ctz
                        exercise_data.days_since_proficient = timedelta.days
	                exercise_data.is_proficient = 1

                    exercise_data.days_since_last_attempted = -1
	            if 'last_done' in exercise and (type(exercise['last_done']) is StringType or type(exercise['last_done']) is UnicodeType):
	                dt_utc = datetime.datetime.strptime(exercise['last_done'], "%Y-%m-%dT%H:%M:%SZ")
                        dt_ctz = dt_utc + datetime.timedelta(minutes=tz_offset_mins)
                        timedelta = dt_now_ctz - dt_ctz
	                exercise_data.days_since_last_attempted = timedelta.days

                    # trying to catch the exercise_states bug
                    exercise_data.status = "working"
	            try:
	                states = exercise['exercise_states']
	            except Exception, e:
	                logging.error("Exercise name: <%s>, error while reading exercise_states %s" % (exercise['exercise'], e))
	            else:
	                for k in ["proficient", "reviewing", "struggling", "suggested"]:
                            if states[k]:
	                        exercise_data.status = k
		                break

                    # Write exercise detail for this student to data store
	            exercise_data.put()

                    if activity_log:
                        exercise_log_data = json.loads(oauth_util.access_resource(
			                       "/api/v1/user/exercises/%s/log?email=%s" % (exercise_data.name,
		                                                                           student_email)))

	                if exercise_log_data is not None:
                            for exercise_log in exercise_log_data:
	                        dt_exercise_log_utc = datetime.datetime.strptime(exercise_log['time_done'],
		                                                                 "%Y-%m-%dT%H:%M:%SZ")
	                        dt_exercise_log_ctz = dt_exercise_log_utc + datetime.timedelta(minutes=tz_offset_mins)
	                        timedelta = dt_now_ctz - dt_exercise_log_ctz
	                        if dt_exercise_log_ctz.hour > 8 and dt_exercise_log_ctz.hour < 16:
		                    if timedelta.days in exercise_activity_in_class:
	                                exercise_activity_in_class[timedelta.days] += exercise_log['time_taken']
		                    else:
	                                exercise_activity_in_class[timedelta.days] = exercise_log['time_taken']
	                        else:
		                    if timedelta.days in exercise_activity_outside_class:
	                                exercise_activity_outside_class[timedelta.days] += exercise_log['time_taken']
		                    else:
	                                exercise_activity_outside_class[timedelta.days] = exercise_log['time_taken']
        
                if activity_log:
                    # today
                    student_data.ea_ic_1 = 0
                    student_data.ea_oc_1 = 0
                    if 0 in exercise_activity_in_class:
                        student_data.ea_ic_1 += exercise_activity_in_class[0]
                    if 0 in exercise_activity_outside_class:
                        student_data.ea_oc_1 += exercise_activity_outside_class[0]
                    student_data.ea_1 = student_data.ea_ic_1+student_data.ea_oc_1
            
                    # last 7 days
                    student_data.ea_ic_7 = student_data.ea_ic_1
                    student_data.ea_oc_7 = student_data.ea_oc_1
                    for i in range(1, 6):
                        if i in exercise_activity_in_class:
                            student_data.ea_ic_7 += exercise_activity_in_class[i]
                        if i in exercise_activity_outside_class:
                            student_data.ea_oc_7 += exercise_activity_outside_class[i]
                    student_data.ea_7 = student_data.ea_ic_7+student_data.ea_oc_7
        
                    # last 30 days
                    student_data.ea_ic_30 = student_data.ea_ic_7
                    student_data.ea_oc_30 = student_data.ea_oc_7
                    for i in range(7, 30):
                        if i in exercise_activity_in_class:
                            student_data.ea_ic_30 += exercise_activity_in_class[i]
                        if i in exercise_activity_outside_class:
                            student_data.ea_oc_30 += exercise_activity_outside_class[i]
                    student_data.ea_30 = student_data.ea_ic_30+student_data.ea_oc_30
        
                    # total
                    student_data.ea_ic = student_data.ea_ic_30
                    student_data.ea_oc = student_data.ea_oc_30
                    for i in range(31, len(exercise_activity_in_class)):
                        if i in exercise_activity_in_class:
                            student_data.ea_ic += exercise_activity_in_class[i]
                        if i in exercise_activity_outside_class:
                            student_data.ea_oc += exercise_activity_outside_class[i]
                    student_data.ea = student_data.ea_ic+student_data.ea_oc
        
    # Write output to data store
    student_data.num_retries = 0
    student_data.put()
    return
	
class ClassData():
    access_token = None
    tz_offset_mins = -420
    coach = ""
    mailto = ""
    activity_log = False
    student_goals = ""
    excel_output = None
    tgz_output = None
    generate_goals_dashboard = False
    unique_key = ""
    list_id = ""

    def run(self):
        # No need for mutex
        state = SavedState()
	state.coach_key = "%s-%s" % (self.coach, self.unique_key)
	state.total_number_of_students = 0
	state.put()

	deferred.defer(class_data_worker, self)

	return

    def send_email_to_coach(self):
        fromaddr = 'no-reply@khanacademy.org'
        toaddr = self.mailto

        self.convert_to_xls()

	# send email
        if not self.generate_goals_dashboard:
	    subject = 'Khan Academy: Class report data in XLS format'
            body = "Hello %s,\n\nPlease find the class report you had requested, attached as an XLS file. If you have any questions or comments please contact implementations@khanacademy.org\n\nCheers!\nKhan Academy Implementation team" % self.coach.split('@')[0]

            filename = "student_data.xls"
	    mail.send_mail(sender=fromaddr,
	            to=toaddr,
	            subject=subject,
	            body=body,
	            attachments=[(filename, self.excel_output.getvalue())])

	    self.excel_output.close()

	else:
	    self.create_tgz()

	    subject = 'Khan Academy: Goals dashboard data'
	    body = "Hello %s,\n\nPlease find the goals dashboard data you had requested, attached as a tar/gzipped (tgz) file. Please follow the steps below to access the data.\n\n1. Save the archive (tgz file) in any directory\n2. Open and extract the two files in the archive\n3. First open the file named student_data.xls, then open the file named goals_dashboard.xls\n4. When you open goals_dashboard.xls, 'Enable Macros' and 'Update Links' (it might be 'Enable Content' and 'Update Content' on some versions of Excel) when asked\n5. Go to the 'Summary' sheet of the goals_dashboard.xls workbook and click on the Recalculate button\n6. You will see your students progress color coded on the 'Dashboard' sheet and the summary on the 'Summary' sheet.\n\nIf you have any questions or comments please contact implementations@khanacademy.org\n\nCheers!\nKhan Academy Implementation team\n\nPS: On Windows machines, if you don't have winzip, you might want to download a version of winzip (a free version may be downloaded from download.cnet.com)" % self.coach.split('@')[0]

            filename = "goals_report.tgz"
	    mail.send_mail(sender=fromaddr,
	            to=toaddr,
	            subject=subject,
	            body=body,
	            attachments=[(filename, self.tgz_output.getvalue())])

	    self.excel_output.close()
	    self.tgz_output.close()

        return

    def convert_to_xls(self):
        # Create the workbook
        wb = Workbook()

        # Write the header rows
        activity_detail_ws = wb.add_sheet('activity_detail')
        ad_row = 0; ad_col = 0
	activity_detail_ws.write(ad_row,ad_col,"Student Email"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Member For"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Energy Points"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Exercises Proficient"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Videos Watched"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Metorite Badges"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Moon Badges"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Earth Badges"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Sun Badges"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Blackhole Badges"); ad_col += 1
	activity_detail_ws.write(ad_row,ad_col,"Challenge Patches"); ad_col += 1
	if self.activity_log:
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity In Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Outside Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity In Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Outside Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity In Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Outside Class Today"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity In Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Outside Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity In Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Outside Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity In Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Outside Class Past 7 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity In Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Outside Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity In Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Outside Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity In Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Outside Class Past 30 days"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity In Class To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Exercise Activity Outside Class To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity In Class To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Video Activity Outside Class To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity In Class To date"); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,"Total Activity Outside Class To date"); ad_col += 1
	ad_row += 1

	exercise_detail_ws = wb.add_sheet('exercise_detail')
	ed_row = 0; ed_col = 0
        exercise_detail_ws.write(ed_row,ed_col,"Student Email"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Exercise Name"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Exercise Display Name"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Key"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Status"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Is Proficient"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Number of Problems Attempted"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Number Correct"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Current Streak"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Longest Streak"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Days Since Proficient"); ed_col += 1
        exercise_detail_ws.write(ed_row,ed_col,"Days Since Last Attempted"); ed_col += 1
	ed_row += 1

        student_emails = []
        if len(self.list_id) > 0 and self.list_id != "allstudents":
            oauth_util = OAuthUtil()
            oauth_util.access_token = self.access_token
            students = json.loads(oauth_util.access_resource(
                                  "/api/v1/user/students?email=%s&list_id=%s" %
                                  (self.coach, self.list_id)))
            if students is not None:
                for student in students:
                    student_emails.append(student['email'])

	query = StudentData.all()
	query.filter('coaches =', self.coach)
        if len(self.list_id) > 0 and self.list_id != "allstudents":
            query.filter('student_email IN', student_emails)
	students_data = query.fetch(1000)

        # Get activity detail and exercise detail
        for student in students_data:
            ad_col = 0
	    activity_detail_ws.write(ad_row,ad_col,student.student_email); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.member_for); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.energy_points); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.exercises_proficient); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.videos_watched); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.meteorite_badges); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.moon_badges); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.earth_badges); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.sun_badges); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.blackhole_badges); ad_col += 1
	    activity_detail_ws.write(ad_row,ad_col,student.challenge_patches); ad_col += 1
	    if self.activity_log:
	        activity_detail_ws.write(ad_row,ad_col,(student.ea_ic_1+student.ea_oc_1)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_ic_1/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_oc_1/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_1+student.va_oc_1)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_ic_1/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_oc_1/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_1+student.ea_1)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_1+student.ea_ic_1)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_oc_1+student.ea_oc_1)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.ea_ic_7+student.ea_oc_7)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_ic_7/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_oc_7/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_7+student.va_oc_7)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_ic_7/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_oc_7/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_7+student.ea_7)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_7+student.ea_ic_7)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_oc_7+student.ea_oc_7)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.ea_ic_30+student.ea_oc_30)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_ic_30/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_oc_30/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_30+student.va_oc_30)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_ic_30/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_oc_30/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_30+student.ea_30)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic_30+student.ea_ic_30)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_oc_30+student.ea_oc_30)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.ea_ic+student.ea_oc)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_ic/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.ea_oc/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic+student.va_oc)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_ic/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,student.va_oc/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va+student.ea)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_ic+student.ea_ic)/60); ad_col += 1
	        activity_detail_ws.write(ad_row,ad_col,(student.va_oc+student.ea_oc)/60); ad_col += 1
	    ad_row += 1

	    query = StudentExerciseData.all()
	    query.filter('student_email =', student.student_email)
	    exercises_data = query.fetch(1000)

	    for exercise in exercises_data:
                ed_col = 0
		exercise_detail_ws.write(ed_row,ed_col,student.student_email); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.name); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.display_name); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.ekey); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.status); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.is_proficient); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.number_of_problems_attempted); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.number_correct); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.current_streak); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.longest_streak); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.days_since_proficient); ed_col += 1
                exercise_detail_ws.write(ed_row,ed_col,exercise.days_since_last_attempted); ed_col += 1
		ed_row += 1

        # Get goals, if any
	if len(self.student_goals) > 0:
            goals_ws = wb.add_sheet('goals')
	    csv_data = StringIO.StringIO(self.student_goals)
	    reader = csv.reader(csv_data)
            row = 0
            for line in reader:
                column = 0
                for cell in line:
                    goals_ws.write(row,column,cell)
                    column += 1
	        row += 1
	    csv_data.close()

        self.excel_output = StringIO.StringIO()
	wb.save(self.excel_output)

        return

    def create_tgz(self):
        self.tgz_output = StringIO.StringIO()

	# create new tarfile object
	tf = tarfile.open(fileobj=self.tgz_output, mode='w:gz')

        # for student_data.xls
	tinfo = tarfile.TarInfo(name="student_data.xls")
	tinfo.size = self.excel_output.len
	self.excel_output.seek(0)
	tf.addfile(tarinfo=tinfo, fileobj=self.excel_output)

	# for goals_dashboard.xls (which is a local file)
        template_tf = tarfile.open("goals_dashboard.xls.tgz", "r:gz")
	template_tinfo = template_tf.getmember("goals_dashboard.xls")
	template_output = template_tf.extractfile("goals_dashboard.xls")
	tf.addfile(tarinfo=template_tinfo, fileobj=template_output)

	template_tf.close()
	tf.close()

	return
