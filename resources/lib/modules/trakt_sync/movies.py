import ast
import copy
from datetime import datetime

from resources.lib.common import tools
from resources.lib.indexers import tmdb
from resources.lib.indexers import trakt, imdb
from resources.lib.modules import trakt_sync


class TraktSyncDatabase(trakt_sync.TraktSyncDatabase):
    def get_movie_list(self, trakt_list):
        if 'movie' in trakt_list:
            trakt_list = [movie['movie'] for movie in trakt_list]

        trakt_list = [movie['ids']['trakt'] for movie in trakt_list]

        statement = 'SELECT * FROM movies WHERE trakt_id in (%s)' % ','.join((str(i) for i in trakt_list))
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute(statement)
        movie_db_list = cursor.fetchall()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)
        requires_update = []

        for movie_id in trakt_list:
            db_item = [i for i in movie_db_list if i['trakt_id'] == movie_id]
            if len(db_item) == 1:
                db_item = db_item[0]
                if db_item['kodi_meta'] == '{}':
                    requires_update.append(movie_id)
            else:
                requires_update.append(movie_id)

        if len(requires_update) == 0:
            meta_list = []
            for movie in movie_db_list:
                if movie['kodi_meta'] == '{}':
                    continue
                meta_list.append(self.movie_db_to_meta(movie))
            return meta_list
        else:
            self.update_movie_list(requires_update)

        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute(statement)
        movie_db_list = cursor.fetchall()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)

        meta_list = []
        for movie in movie_db_list:
            if movie['kodi_meta'] == '{}':
                continue
            meta_list.append(self.movie_db_to_meta(movie))
        return meta_list

    def update_movie_list(self, id_list):
        self.item_list = []
        for movie in id_list:
            self.task_queue.put(self.get_movie, movie, False, True)

        self.task_queue.wait_completion()

    def movie_db_to_meta(self, movie_object):
        watched = movie_object['watched']
        movie_meta = ast.literal_eval(movie_object['kodi_meta'])
        movie_meta['info']['playcount'] = watched
        return movie_meta

    def get_all_movies(self):
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('SELECT * FROM movies')
        movies = cursor.fetchall()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)
        movies = [i['trakt_id'] for i in movies]
        return movies

    def get_watched_movies(self):
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('SELECT * FROM movies WHERE watched =1')
        movies = cursor.fetchall()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)
        return movies

    def get_collected_movies(self):
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('SELECT * FROM movies WHERE collected =1')
        movies = cursor.fetchall()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)
        return movies

    def mark_movie_watched(self, trakt_id):
        self._mark_movie_record('watched', 1, trakt_id)

    def mark_movie_unwatched(self, trakt_id):
        self._mark_movie_record('watched', 0, trakt_id)

    def mark_movie_collected(self, trakt_id):
        self._mark_movie_record('collected', 1, trakt_id)

    def mark_movie_uncollected(self, trakt_id):
        self._mark_movie_record('collected', 0, trakt_id)

    def _mark_movie_record(self, column, value, trakt_id):
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('UPDATE movies SET %s=? WHERE trakt_id=?' % column, (value, trakt_id))
        cursor.connection.commit()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)

    def get_movie(self, trakt_id, list_mode=False, get_meta=True, watched=None, collected=None):
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('SELECT * FROM movies WHERE trakt_id=? ', (trakt_id,))
        item = cursor.fetchone()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)

        if item is None:

            movie_object = trakt.TraktAPI().json_response('/movies/%s?extended=full' % trakt_id)

            if movie_object is None:
                return

            item = self._update_movie(movie_object, get_meta)

        else:
            if item['kodi_meta'] != '{}':
                item['kodi_meta'] = ast.literal_eval(item['kodi_meta'])
            else:
                if get_meta:
                    movie_object = trakt.TraktAPI().json_response('/movies/%s?extended=full' % trakt_id)
                    if movie_object is None:
                        return

                    item = self._update_movie(movie_object, get_meta)
                else:
                    item['kodi_meta'] = ast.literal_eval(item['kodi_meta'])

        if item is None:
            return

        if item['collected'] == 0 and collected == 1:
            self._mark_movie_record('collected', 1, item['trakt_id'])
            item['collected'] = 1

        if item['watched'] == 0 and watched == 1:
            self._mark_movie_record('watched', 1, item['trakt_id'])
            item['watched'] = 1

        try:
            if item['watched'] == 1:
                item['kodi_meta']['info']['playcount'] = 1
            else:
                item['kodi_meta']['info']['playcount'] = 0
        except:
            pass

        if list_mode:
            self.item_list.append(copy.deepcopy(item['kodi_meta']))
        else:
            return item['kodi_meta']
        pass

    def _update_movie(self, trakt_object, get_meta=True):
        movie_id = trakt_object['ids']['trakt']
        update_time = str(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))
        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()
        cursor.execute('SELECT * FROM movies WHERE trakt_id=?', (int(movie_id),))
        old_entry = cursor.fetchone()
        cursor.close()
        tools.try_release_lock(tools.traktSyncDB_lock)

        if get_meta and (old_entry is None or old_entry['kodi_meta'] == '{}'):
            try:
                kodi_meta = tmdb.TMDBAPI().movieToListItem(trakt_object)
                if kodi_meta is None or kodi_meta == '{}':
                    kodi_meta = imdb.IMDBScraper().movieToListItem(trakt_object)
            except:
                return None
        else:
            if old_entry is None:
                kodi_meta = {}
            else:
                kodi_meta = old_entry['kodi_meta']

        if kodi_meta is None:
            return

        if old_entry is None:
            air_date = kodi_meta['info'].get('aired', kodi_meta['info']['premiered'])
            old_entry = {'collected': 0, 'watched': 0, 'air_date': air_date}

        collected = old_entry['collected']
        watched = old_entry['watched']
        air_date = old_entry['air_date']

        tools.traktSyncDB_lock.acquire()
        cursor = self._get_cursor()

        try:
            cursor.execute(
                "REPLACE INTO movies ("
                "trakt_id, kodi_meta, collected, watched, last_updated, air_date)"
                "VALUES "
                "(?, ?, ?, ?, ?, ?)",
                (movie_id, str(kodi_meta), collected, watched, update_time, air_date))
            cursor.connection.commit()
            cursor.close()

            return {'trakt_id': movie_id, 'kodi_meta': kodi_meta,
                    'update_time': update_time, 'watched': watched, 'collected': collected}
        except:
            cursor.close()
            import traceback
            traceback.print_exc()
            pass
        finally:
            tools.try_release_lock(tools.traktSyncDB_lock)
