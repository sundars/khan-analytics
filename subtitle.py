import cgi
import logging
import simplejson as json
import os
import string
import urllib

from oauth_util import OAuthUtil

from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext import deferred
from google.appengine import runtime
from google.appengine.ext import blobstore
from google.appengine.api import files

from pyExcelerator import *
from types import *
import tarfile
import random
import time
import StringIO

class LanguageData(db.Model):
    language = db.StringProperty()
    lang_in_native = db.StringProperty()
    translated_videos = db.IntegerProperty()
    last_week_incremental = db.ListProperty(item_type=int)

class SubtitleData(db.Model):
    youtube_id = db.StringProperty()
    video = db.StringProperty()
    playlist = db.StringProperty()
    language = db.StringProperty()
    percent_complete = db.IntegerProperty()
    language_code = db.StringProperty()
    language_id = db.IntegerProperty()
    video_id = db.StringProperty()

class PlayListData(db.Model):
    playlist = db.StringProperty()
    video_count = db.IntegerProperty()
    subtitled_in_english_count = db.IntegerProperty()
    subtitle_languages = db.StringListProperty()

class PlayListProcessedState(db.Model):
    unique_key = db.StringProperty()
    pkey = db.StringProperty()
    number_of_attempts = db.IntegerProperty()
    videos_processed = db.StringListProperty()
    count_of_videos_processed = db.IntegerProperty()

class FinishUpdateState(db.Model):
    unique_key = db.StringProperty()
    videos_processed = db.StringListProperty()
    number_of_videos_processed = db.IntegerProperty()

class BlobStoreData(db.Model):
    nickname = db.StringProperty()
    blobkey = blobstore.BlobReferenceProperty()

MATH_PLAYLISTS = ['Arithmetic', 'Core Pre-algebra', 'Core Algebra', 'Algebra I',
                  'Developmental Math 1', 'Developmental Math 2', 'Developmental Math 3',
                  'Worked Examples 1', 'Worked Examples 2', 'Worked Examples 3',
                  'Worked Examples 4', 'Geometry', 'Precalculus', 'Trigonometry',
                  'Calculus', 'Probability', 'Statistics', 'Linear Algebra', 'Differential Equations']

TOP_LANGUAGES = ['Arabic', 'Bengali', 'Bulgarian', 'Chinese Simplified', 'Creole Haitian',
                 'Czech', 'Dutch', 'English', 'French', 'German', 'Greek', 'Gujarati', 'Hebrew',
                 'Hindi', 'Hungarian', 'Indonesian', 'Italian', 'Japanese', 'Kannada', 'Korean', 'Malay',
                 'Malayalam', 'Marathi', 'Mongolian', 'Norwegian Nynorsk', 'Oromo', 'Pashto',
                 'Persian', 'Polish', 'Portuguese', 'Punjabi', 'Russian', 'Serbian', 'Spanish',
                 'Swahili', 'Swedish', 'Tamil', 'Telugu', 'Thai', 'Turkish', 'Ukrainian',
                 'Urdu', 'Vietnamese']

IGNORE_LANGUAGES = ['English', 'Portuguese Brazilian', 'English British']

def prepare_download_data_worker():
    logging.info("Preparing download data")
    wb = Workbook()
    playlists_ws = wb.add_sheet("Playlists")
    pl_row = 0; pl_col = 0
    playlists_ws.write(pl_row,pl_col,"Playlist"); pl_col += 1
    playlists_ws.write(pl_row,pl_col,"Total # of videos"); pl_col += 1
    pl_row += 1

    subtitles_ws = wb.add_sheet("Subtitles")
    sd_row = 0; sd_col = 0
    subtitles_ws.write(sd_row,sd_col,"Playlist"); sd_col +=1
    subtitles_ws.write(sd_row,sd_col,"Video"); sd_col +=1
    subtitles_ws.write(sd_row,sd_col,"Youtube_ID"); sd_col +=1
    subtitles_ws.write(sd_row,sd_col,"Language"); sd_col +=1
    subtitles_ws.write(sd_row,sd_col,"Completed"); sd_col +=1
    sd_row += 1

    query = PlayListData.all()
    pldata = query.fetch(1000)
    for pl in pldata:
        pl_col = 0
        playlists_ws.write(pl_row,pl_col,pl.playlist); pl_col += 1
        playlists_ws.write(pl_row,pl_col,pl.video_count); pl_col += 1
        pl_row += 1

        query = SubtitleData.all()
        query.filter('playlist =', pl.playlist)
        sdata = query.fetch(1000)
        cursor = query.cursor()
        while (len(sdata) > 0):
            logging.info("Processing playlist %s with %d videos" % (pl.playlist, len(sdata)))
            for s in sdata:
                sd_col = 0
                subtitles_ws.write(sd_row,sd_col,s.playlist); sd_col +=1
                subtitles_ws.write(sd_row,sd_col,s.video); sd_col +=1
                subtitles_ws.write(sd_row,sd_col,s.youtube_id); sd_col +=1
                subtitles_ws.write(sd_row,sd_col,s.language); sd_col +=1
                subtitles_ws.write(sd_row,sd_col,s.percent_complete); sd_col +=1
                sd_row += 1
            query = SubtitleData.all()
            query.filter('playlist =', pl.playlist)
            query.with_cursor(cursor)
            sdata = query.fetch(1000)
            cursor = query.cursor()
        
    blobstoredata = BlobStoreData.all().filter('nickname =', 'download_file').get()
    if blobstoredata is not None:
        blobstoredata.delete()

    excel_output = StringIO.StringIO()
    wb.save(excel_output)

    data = excel_output.getvalue()
    # write to blobstore
    file_name = files.blobstore.create(mime_type='application/octet-stream')

    pos = 0
    chunkSize = 65536
    f = files.open(file_name, 'a')
    while pos < len(data):
        chunk = data[pos:pos+chunkSize]
        pos += chunkSize
        f.write(chunk)
    f.close()

    files.finalize(file_name)
    blob_key = files.blobstore.get_blob_key(file_name)
    excel_output.close()

    blobstoredata = BlobStoreData()
    blobstoredata.nickname = 'download_file'
    blobstoredata.blobkey = blob_key
    blobstoredata.put()

    return

def update_language_count_worker():
    all_languages = []
    query = PlayListData.all()
    pldata = query.fetch(1000)
    for pl in pldata:
        all_languages.extend(pl.subtitle_languages)

    s = set(all_languages)
    all_languages = list(s)
    if "" in all_languages:
        all_languages.remove("")

    all_languages.sort()

    for lang in all_languages:
        translated_videos = 0
        query = SubtitleData.all()
        query.filter('language =', lang)
        query.filter('percent_complete =', 100)
        qcount = query.count(1000)
        cursor = query.cursor()
        while qcount > 0:
            translated_videos += qcount
            query = SubtitleData.all()
            query.filter('language =', lang)
            query.filter('percent_complete =', 100)
            query.with_cursor(cursor)
            qcount = query.count(1000)
            cursor = query.cursor()

        ldata = LanguageData.all().filter('language =', lang).get()
        if ldata is None:
            ldata = LanguageData()
            ldata.translated_videos = 0
            ldata.language = lang
            ldata.last_week_incremental = [0, 0, 0, 0, 0, 0, 0]

        try:
            last_day_incremental = translated_videos - ldata.translated_videos
        except Exception, e:
            logging.info("Failed to update incremental for language %s" % lang)

        ldata.last_week_incremental.pop(0)
        ldata.last_week_incremental.append(last_day_incremental)
        ldata.translated_videos = translated_videos

        ldata.put()

def finish_subtitle_update_worker(unique_key):
    query = PlayListProcessedState.all()
    query.filter('unique_key =', unique_key)
    pstate_list = query.fetch(50)

    if len(pstate_list) > 0:
        time.sleep(10)
        deferred.defer(finish_subtitle_update_worker, unique_key)
        return

    try:
        finish_subtitle_update(unique_key)
    except runtime.DeadlineExceededError:
        logging.info("DeadlineExceeded Error finishing subtitle data update")
        deferred.defer(finish_subtitle_update_worker, unique_key)
    except Exception, e:
        logging.info("Error finishing subtitle data update <%s>" % e)
        deferred.defer(finish_subtitle_update_worker, unique_key)

def finish_subtitle_update(unique_key):
    oauth_util = OAuthUtil()

    query = FinishUpdateState.all()
    query.filter('unique_key =', unique_key)
    finish_state = query.get()

    query = SubtitleData.all()
    query.filter('language =', 'English')
    for sdata in query:
        if sdata.youtube_id in finish_state.videos_processed:
            continue

        try:
            usub_video = json.loads(oauth_util.access_usub_resource(
                                    "/api/1.0/video/",
                                    sdata.youtube_id, True))
        except Exception, e:
            logging.info("Unable to fetch video_id for video <%s> in playlist <%s> from universal subtitles" %
                         (sdata.youtube_id, sdata.playlist))
            continue

        # A video could be in multiple playlists, so get all of them
        query2 = SubtitleData.all()
        query2.filter('language =', 'English')
        query2.filter('youtube_id =', sdata.youtube_id)
        for sdata2 in query2:
            sdata2.video_id = usub_video['video_id']
            sdata2.put()

        finish_state.videos_processed.append(sdata.youtube_id)
        finish_state.number_of_videos_processed += 1
        finish_state.put()

    finish_state.delete()
    deferred.defer(update_language_count_worker)
    deferred.defer(prepare_download_data_worker)
    return

def populate_subtitle_data_worker(playlist, pkey):
    sleeptime = random.randint(1, 7)
    time.sleep(sleeptime)

    try:
        populate_subtitle_data(playlist, pkey)
    except runtime.DeadlineExceededError:
        logging.info("DeadlineExceeded error processing playlist %s:" % playlist)
        query = PlayListProcessedState.all()
        query.filter('pkey =', pkey)
        pstate = query.get()
        pstate.number_of_attempts += 1
        pstate.put()
        if pstate.number_of_attempts > 5:
            time.sleep(10)
        deferred.defer(populate_subtitle_data_worker, playlist, pkey)
    except Exception, e:
        logging.info("Error processing playlist %s: <%s>" % (playlist, e))
        query = PlayListProcessedState.all()
        query.filter('pkey =', pkey)
        pstate = query.get()
        pstate.number_of_attempts += 1
        pstate.put()
        if pstate.number_of_attempts > 5:
            time.sleep(10)
        deferred.defer(populate_subtitle_data_worker, playlist, pkey)

    return

def populate_subtitle_data(playlist, pkey):
    logging.info("Updating playlist <%s>" % playlist)
    query = PlayListProcessedState.all()
    query.filter('pkey =', pkey)
    pstate = query.get()

    oauth_util = OAuthUtil()
    ka_videos = json.loads(oauth_util.access_resource(
                           "/api/v1/playlists/%s/videos" % urllib.quote(playlist)))
    logging.info("Fetched <%d> videos from KA for playlist <%s>" % (len(ka_videos), playlist))

    query = PlayListData.all()
    query.filter('playlist =', playlist)
    pldata = query.get()
    if pldata is None:
        pldata = PlayListData()
        pldata.subtitle_languages = []
        pldata.playlist = playlist
        pldata.subtitled_in_english_count = 0

    pldata.video_count = len(ka_videos)
    lang_mapped = {}
    for ka_video in ka_videos:
        if ka_video['youtube_id'] in pstate.videos_processed:
            continue

        try:
            languages = json.loads(oauth_util.access_usub_resource(
                                   "/api/1.0/subtitles/languages/",
                                   ka_video['youtube_id'], False))
        except Exception, e:
            continue

        if type(languages) is not ListType:
            continue

        for language in languages:
            lang = string.replace(language['name'],",","")
            lang_in_native = lang
            if language['name'].find('(') >= 0:
                lang_in_native = language['name'].split('(')[1].split(')')[0]
                lang_in_native = string.replace(lang_in_native,",","")
                lang = language['name'].split('(')[0].strip()
                lang = string.replace(lang,",","")

            if lang not in lang_mapped:
                lang_mapped[lang] = True
                ldata = LanguageData.all().filter('language =', lang).get()
                if ldata is None:
                    ldata = LanguageData()
                    ldata.language = lang
                    ldata.lang_in_native = lang_in_native
                    ldata.translated_videos = 0
                    ldata.last_week_incremental = [0, 0, 0, 0, 0, 0, 0]
                    ldata.put()

                else:
                    if ldata.lang_in_native != lang_in_native:
                        ldata.lang_in_native = lang_in_native
                        ldata.language = lang
                        ldata.put()

            pldata.subtitle_languages.append(lang)

            query = SubtitleData.all()
            query.filter('youtube_id =',ka_video['youtube_id'])
            query.filter('language =', lang)
            query.filter('playlist =', playlist)
            sdata = query.get()
            if sdata is None:
                sdata = SubtitleData()
                sdata.youtube_id = ka_video['youtube_id']
                sdata.video = ka_video['title']
                sdata.playlist = playlist
                sdata.language = lang
                sdata.percent_complete = 0
                sdata.language_code = language['code']
                sdata.language_id = language['id']
                sdata.video_id = ""

            s = language['completion']
            try:
                string.index(s," Line")
            except ValueError:
                try:
                    string.index(s, "%")
                except ValueError:
                    percent_complete = 0
                else:
                    percent_complete = int(string.rstrip(s," %"))
            else:
                i = int(string.rstrip(s," Lines"))
                if i == 0:
                    percent_complete = 0
                elif lang == 'English':
                    percent_complete = 100
                else:
                    percent_complete = 50

            if percent_complete > sdata.percent_complete:
                sdata.percent_complete = percent_complete
                sdata.language_id = language['id']

            sdata.put()

        pstate.videos_processed.append(ka_video['youtube_id'])
        pstate.count_of_videos_processed += 1
        pstate.put()

        # uniquify the list of languages interested in this playlist
        s = set(pldata.subtitle_languages)
        pldata.subtitle_languages = list(s)
        pldata.put()

    # Delete those entities that have been removed from this playlist, should happen less often
    query = SubtitleData.all()
    query.filter('playlist =', playlist)
    for sdata in query:
        if sdata.youtube_id not in pstate.videos_processed:
            sdata.delete()

    query = SubtitleData.all()
    query.filter('playlist =', playlist)
    query.filter('language =', 'English')
    query.filter('percent_complete =', 100)
    sl = query.fetch(5000)
    pldata.subtitled_in_english_count = len(sl)
    pldata.put()

    pstate.delete()
    return

class UpdateSubtitles(webapp.RequestHandler):
    def get(self):
        logging.info("Accessing %s" % self.request.path)

        oauth_util = OAuthUtil()
        get_playlists = True
        while get_playlists:
            try:
                playlists = json.loads(oauth_util.access_resource("/api/v1/playlists"))
                get_playlists = False
            except Exception, e:
                logging.error("/api/v1/playlists failed with error: %s" % e)

        # Remove stale playlists
        all_pld = PlayListData.all().fetch(1000)
        stored_pl_titles = []
        for pld in all_pld:
            stored_pl_titles.append(pld.playlist)

        live_pl_titles = []
        for playlist in playlists:
            live_pl_titles.append(playlist['title'])

        s = set(live_pl_titles)
        l = stored_pl_titles
        stale_pl_titles = [x for x in l if x not in s]
        
        logging.info("Stale playlists are %s" % stale_pl_titles)
        stale_plds = PlayListData.all().filter('playlist IN', stale_pl_titles).fetch(1000)
        logging.info("About to delete <%d> PlayListData elements" % len(stale_plds))
        db.delete(stale_plds)

        for pl_title in stale_pl_titles:
            query = SubtitleData.all().filter('playlist =', pl_title)
            stale_sds = query.fetch(1000)
            cursor = query.cursor()
            while len(stale_sds) > 0:
                logging.info("About to delete <%d> SubtitleData elements" % len(stale_sds))
                db.delete(stale_sds)
                query = SubtitleData.all().filter('playlist =', pl_title).with_cursor(cursor)
                stale_sds = query.fetch(1000)
                cursor = query.cursor()

        unique_key = ''.join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for x in range(10))
        for playlist in playlists:
            pkey = playlist['title'] + unique_key
            pstate = PlayListProcessedState()
            pstate.unique_key = unique_key
            pstate.pkey = pkey
            pstate.number_of_attempts = 0
            pstate.videos_processed = []
            pstate.count_of_videos_processed = 0
            pstate.put()
            deferred.defer(populate_subtitle_data_worker, playlist['title'], pkey)

        finish_state = FinishUpdateState()
        finish_state.unique_key = unique_key
        finish_state.number_of_videos_processed = 0
        finish_state.videos_processed = []
        finish_state.put()
        deferred.defer(finish_subtitle_update_worker, unique_key)

        self.response.set_status(200)
        self.response.out.write("OK")
        return

def get_random_video_to_subtitle(playlist, language):
    oauth_util = OAuthUtil()
    if playlist == "all":
        all_playlists = json.loads(oauth_util.access_resource(
                                   "/api/v1/playlists"))
        playlists = []
        for pl in all_playlists:
            if pl['title'] in MATH_PLAYLISTS:
                playlists.append(pl['title'])
    else:
        playlists = [ playlist ]

    query = SubtitleData.all()
    query.filter('language =', language)
    tmp = query.fetch(2)
    language_code = tmp[0].language_code

    for pl in playlists:
        query = SubtitleData.all()
        query.filter('language =', language)
        query.filter('playlist =', pl)
        sdata_list = query.fetch(1000)

        yid_list = []
        yid_list_all = []
        for sdata in sdata_list:
            yid_list_all.append(sdata.youtube_id)
            if sdata.percent_complete < 100:
                yid_list.append(sdata.youtube_id)

        ka_videos = json.loads(oauth_util.access_resource(
                               "/api/v1/playlists/%s/videos" %
                               urllib.quote(pl)))

        klist = []
        for ka_video in ka_videos:
            klist.append(ka_video['youtube_id'])

        s = set(yid_list_all)
        l = klist
        klist = [x for x in l if x not in s]
        yid_list.extend(klist)

        random.shuffle(yid_list)
        for yid in yid_list:
            query = SubtitleData.all()
            query.filter('playlist =', pl)
            query.filter('language =', 'English')
            query.filter('youtube_id =', yid)
            english_data = query.get()

            if english_data is None:
                break

            if language != 'English':
                if english_data.percent_complete == 0:
                    continue

                # Found a random not completely subtitled video, with english done
                # Generate the URL
                url = "http://www.universalsubtitles.org/en/onsite_widget/?config={"
                url += '"videoID":"' + english_data.video_id + '"'
                url += ',"videoURL":"http://www.youtube.com/watch?v=' + yid + '"'
                url += ',"effectiveVideoURL":"http://www.youtube.com/watch?v=' + yid + '"'
                url += ',"languageCode":"' + language_code + '"'
                url += ',"originalLanguageCode":null'
                url += ',"subLanguagePK":null'
                url += ',"baseLanguagePK":"%d"}' % english_data.language_id

            else:
                # Found a random English video that isn't done
                # Generate the URL
                url = "http://www.universalsubtitles.org/en/videos/"
                url += english_data.video_id

            return url

    return None

class SubtitleActions(webapp.RequestHandler):
    def get(self):
        random_video_to_subtitle = self.request.get('random')
        playlist = self.request.get('playlist')
        language = self.request.get('language')

        if not playlist or not language:
            self.redirect("/translations/subtitlestatus")

        if language == "all":
            self.redirect("/translations/subtitlestatus")

        if random_video_to_subtitle:
            url = get_random_video_to_subtitle(playlist, language)
            if url is not None:
                self.redirect(url)
                return
            else:
                if playlist == "all":
                    response = "All videos in the MATH playlists and language '"
                    response += language
                    response += "' have been subtitled, pick another playlist"
                else:
                    response = "All videos for playlist '"
                    response += playlist
                    response += "' and language '"
                    response += language
                    response += "' have been subtitled" 

                self.response.set_status(200)
                self.response.out.write(response)
                return

        if playlist == "all":
            self.redirect("/translations/subtitlestatus")

        query = SubtitleData.all()
        query.filter('language =', language)
        query.filter('playlist =', playlist)
        sdata_list = query.fetch(1000)

        if not sdata_list:
            query = SubtitleData.all()
            query.filter('language =', language)
            tmp = query.fetch(2)
            language_code = tmp[0].language_code
        else:
            language_code = sdata_list[0].language_code

        percent_complete = {}
        for sdata in sdata_list:
            percent_complete[sdata.youtube_id] = sdata.percent_complete

        oauth_util = OAuthUtil()
        ka_videos = json.loads(oauth_util.access_resource(
                               "/api/v1/playlists/%s/videos" %
                               urllib.quote(playlist)))

        output_list = []
        for ka_video in ka_videos:
            query = SubtitleData.all()
            query.filter('playlist =', playlist)
            query.filter('language =', 'English')
            query.filter('youtube_id =', ka_video['youtube_id'])
            english_data = query.get()

            if english_data is None:
                continue

            pc = 0
            english_pc = True
            if english_data.percent_complete == 0:
                english_pc = False

            if ka_video['youtube_id'] in percent_complete:
                if percent_complete[ka_video['youtube_id']] == 100:
                    continue

                pc = percent_complete[ka_video['youtube_id']]

            output = {
                'video_id' : english_data.video_id,
                'youtube_id' : ka_video['youtube_id'],
                'percent_complete' : pc,
                'video' : ka_video['title'],
                'language_code' : language_code,
                'base_language_id' : english_data.language_id,
                'english_complete' : english_pc
            }

            output_list.append(output)

        sorted_list = sorted(output_list, key=lambda k: k['percent_complete'])
        template_values = {
            'output_list' : sorted_list,
            'language' : language,
            'playlist' : playlist
        }

        self.response.out.write(template.render('subtitleactions.html', template_values))
        return

class SubtitleStatus(webapp.RequestHandler):
    def get_playlists_and_languages(self):
        all_languages = []
        all_playlists = []
        query = PlayListData.all()
        pldata = query.fetch(1000)
        for pl in pldata:
            all_languages.extend(pl.subtitle_languages)

        s = set(all_languages)
        all_languages = list(s)
        if "" in all_languages:
            all_languages.remove("")

        all_languages.sort()
        all_playlists = sorted(pldata, key=lambda k: k.playlist)

        return (all_playlists, all_languages)

    def get_subtitle_detail_for_language(self, language, playlists):
        pl_stat_for_lang_list = []
        for pld in playlists:
            playlist = pld.playlist
            query = SubtitleData.all()   
            query.filter('playlist =', playlist)
            query.filter('language =', language)
            subtitled_videos = query.fetch(1000)

            pl_stat_for_lang = {
                'playlist':playlist,
                'total_videos':pld.video_count,
                'total_translated':0,
                'translated_100':0,
                'translated_Q4':0,
                'translated_Q3':0,
                'translated_Q2':0,
                'translated_Q1':0,
                'translated_0':0,
                'percent_of_total':0,
                'percent_of_english':0
            }

            for subtitled_video in subtitled_videos:
                if subtitled_video.percent_complete == 100:
                    pl_stat_for_lang['translated_100'] += 1
                elif subtitled_video.percent_complete >= 75:
                    pl_stat_for_lang['translated_Q4'] += 1
                elif subtitled_video.percent_complete >= 50:
                    pl_stat_for_lang['translated_Q3'] += 1
                elif subtitled_video.percent_complete >= 25:
                    pl_stat_for_lang['translated_Q2'] += 1
                elif subtitled_video.percent_complete > 0:
                    pl_stat_for_lang['translated_Q1'] += 1
                else:
                    pl_stat_for_lang['translated_0'] += 1

            if pld.video_count == 0:
                pl_stat_for_lang['percent_of_total'] = 0
            else:
                pl_stat_for_lang['percent_of_total'] = int(float(pl_stat_for_lang['translated_100']*100)/
                                                           float(pld.video_count))
            if pld.subtitled_in_english_count == 0:
                pl_stat_for_lang['percent_of_english'] = 0
            else:
                pl_stat_for_lang['percent_of_english'] = int(float(pl_stat_for_lang['translated_100']*100)/
                                                             float(pld.subtitled_in_english_count))

            pl_stat_for_lang_list.append(pl_stat_for_lang)

        return pl_stat_for_lang_list

    def get_subtitle_detail_for_playlist(self, playlist):
        query = PlayListData.all()
        query.filter('playlist =', playlist)
        pld = query.get()

        languages = []
        language = self.request.get('language')
        if language == 'all':
            languages = pld.subtitle_languages
        else:
            languages.append(language)
        
        if "" in languages:
            languages.remove("")

        languages.sort()

        lang_stat_for_pl_list = []
        for language in languages:
            query = SubtitleData.all()   
            query.filter('playlist =', playlist)
            query.filter('language =', language)
            subtitled_videos = query.fetch(1000)

            lang_stat_for_pl = {
                'language':language,
                'total_translated':0,
                'translated_100':0,
                'translated_Q4':0,
                'translated_Q3':0,
                'translated_Q2':0,
                'translated_Q1':0,
                'translated_0':0,
                'percent_of_total':0,
                'percent_of_english':0
            }

            for subtitled_video in subtitled_videos:
                if subtitled_video.percent_complete == 100:
                    lang_stat_for_pl['translated_100'] += 1
                elif subtitled_video.percent_complete >= 75:
                    lang_stat_for_pl['translated_Q4'] += 1
                elif subtitled_video.percent_complete >= 50:
                    lang_stat_for_pl['translated_Q3'] += 1
                elif subtitled_video.percent_complete >= 25:
                    lang_stat_for_pl['translated_Q2'] += 1
                elif subtitled_video.percent_complete > 0:
                    lang_stat_for_pl['translated_Q1'] += 1
                else:
                    lang_stat_for_pl['translated_0'] += 1

            if pld.video_count == 0:
                lang_stat_for_pl['percent_of_total'] = 0
            else:
                lang_stat_for_pl['percent_of_total'] = int(float(lang_stat_for_pl['translated_100']*100)/
                                                           float(pld.video_count))
            if pld.subtitled_in_english_count == 0:
                lang_stat_for_pl['percent_of_english'] = 0
            else:
                lang_stat_for_pl['percent_of_english'] = int(float(lang_stat_for_pl['translated_100']*100)/
                                                             float(pld.subtitled_in_english_count))

            lang_stat_for_pl_list.append(lang_stat_for_pl)

        return (lang_stat_for_pl_list, pld.video_count)

    def get(self):
        logging.info("Accessing %s" % self.request.path)
        download = self.request.get('download')
        display = self.request.get('display')

        if download:
            blobstoredata = BlobStoreData.all().filter('nickname =', 'download_file').get()
            if blobstoredata is None:
                logging.info("Unable to find blobstore information for downloading all subtitles")
                deferred.defer(prepare_download_data_worker)
                self.response.out.write("Couldn't generate download file now, please try again in a few minutes")
                return

            blob_reader = blobstore.BlobReader(blobstoredata.blobkey)
            data = blob_reader.read()

            excel_output = StringIO.StringIO(data)

            tgz_output = StringIO.StringIO()
            tf = tarfile.open(fileobj=tgz_output, mode='w:gz')

            tinfo = tarfile.TarInfo(name="subtitle_data.xls")
            tinfo.size = excel_output.len
            excel_output.seek(0)
            tf.addfile(tarinfo=tinfo, fileobj=excel_output)

            template_tf = tarfile.open("subtitles.xls.tgz", "r:gz")
            template_tinfo = template_tf.getmember("subtitles.xls")
            template_output = template_tf.extractfile("subtitles.xls")
            tf.addfile(tarinfo=template_tinfo, fileobj=template_output)

            template_tf.close()
            tf.close()

            self.response.headers['Content-Type'] = "application/x-tar"
            self.response.headers['Content-Disposition'] = "attachment; filename=subtitles.tgz"
            self.response.out.write(tgz_output.getvalue())
            return

        elif display:
            playlist = self.request.get('playlist')
            language = self.request.get('language')
            if playlist == 'all' and language == 'all':
                # Cannot process all in display mode, need to download that data
                # Check exits in javascript so shouldn't happen, log it just in case
                logging.error("Subtitle status: Multi playlist display being sought")
                return

            all_playlists, all_languages = self.get_playlists_and_languages()
            all_playlist_names = []
            for pld in all_playlists:
                all_playlist_names.append(pld.playlist)

            if playlist != 'all':
                lang_stat_for_pl_list, pl_video_count = self.get_subtitle_detail_for_playlist(playlist)
                template_values = {
                    "total_videos" : pl_video_count,
                    "languages" : all_languages,
                    "playlists" : all_playlist_names,
                    "display" : 1,
                    "language_table" : 1,
                    "playlist" : json.dumps(playlist),
                    "language" : json.dumps(language),
                    "languages_stats" : lang_stat_for_pl_list,
                    "pl" : playlist
                }
            else:
                pl_stat_for_lang_list = self.get_subtitle_detail_for_language(language, all_playlists)
                template_values = {
                    "languages" : all_languages,
                    "playlists" : all_playlist_names,
                    "display" : 1,
                    "playlist_table" : 1,
                    "playlist" : json.dumps(playlist),
                    "language" : json.dumps(language),
                    "playlists_stats" : pl_stat_for_lang_list,
                    "lang" : language
                }

        else:
            all_playlists, all_languages = self.get_playlists_and_languages()
            all_playlist_names = []
            for playlist in all_playlists:
                all_playlist_names.append(playlist.playlist)

            template_values = {
                "languages" : all_languages,
                "playlists" : all_playlist_names,
                "language" : json.dumps(""),
                "playlist" : json.dumps(""),
            }

        self.response.out.write(template.render('subtitlestatus.html', template_values))
        return

class GetSubtitleLanguages(webapp.RequestHandler):
    def get(self):
        output_str = json.dumps(TOP_LANGUAGES)

        self.response.set_status(200)
        callback = self.request.get('callback')
        if callback:
            self.response.out.write("%s(%s)" % (callback, output_str))
        else:
            self.response.out.write(output_str)

        return

class GetSubtitleLanguagesCount(webapp.RequestHandler):
    def get(self):
        ldata_list = LanguageData.all().fetch(1000)
        sorted_ldata_list = sorted(ldata_list, key=lambda k: k.translated_videos, reverse=True)
        sorted_ldata_last_week_list = sorted(ldata_list, key=lambda k: sum(k.last_week_incremental), reverse=True)

        top_languages = []
        for ldata in sorted_ldata_list:
            if ldata.language in IGNORE_LANGUAGES:
                continue

            top_languages.append(ldata.language)
            if len(top_languages) == 12:
                break

        top_languages_last_week = []
        last_week_subtitled = []
        for ldata in sorted_ldata_last_week_list:
            if ldata.language in IGNORE_LANGUAGES:
                continue

            top_languages_last_week.append(ldata.language)
            last_week_subtitled.append(sum(ldata.last_week_incremental))
            if len(top_languages_last_week) == 12:
                break

        s = set(TOP_LANGUAGES)
        missing_languages = [x for x in top_languages if x not in s]
        TOP_LANGUAGES.extend(missing_languages)

        s = set(TOP_LANGUAGES)
        missing_languages = [x for x in top_languages_last_week if x not in s]
        TOP_LANGUAGES.extend(missing_languages)

        ldata_list = []
        for lang in TOP_LANGUAGES:
            ldata = LanguageData.all().filter('language =', lang).get()
            ldata_list.append(ldata)

        output = []
        ldata_list = sorted(ldata_list, key=lambda k: k.translated_videos, reverse=True)
        for ldata in ldata_list:
            l = {
                'language': ldata.language.strip(),
                'translated_videos': ldata.translated_videos if ldata is not None else 0,
                'last_week': max(0,sum(ldata.last_week_incremental)) if ldata is not None else 0,
                'chart': True if ldata.language in top_languages else False,
                'last_week_languages': top_languages_last_week,
                'last_week_subtitled': last_week_subtitled
            }

            output.append(l)

        output_str = json.dumps(output)

        self.response.set_status(200)
        callback = self.request.get('callback')
        if callback:
            self.response.out.write("%s(%s)" % (callback, output_str))
        else:
            self.response.out.write(output_str)

        return

