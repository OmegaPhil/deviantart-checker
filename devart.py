'''
Copyright (c) 2014-2016, OmegaPhil - OmegaPhil@startmail.com

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import collections
import datetime
import io
import os.path
import traceback
import urllib.parse

import bs4  # Beautiful Soup 4
import requests
import yaml


COMMENTS = 0
REPLIES = 1
UNREAD_NOTES = 2
DEVIATIONS = 3


# Getting new-style class
class AccountState(object):
    '''Maintains the current state of the deviantART account'''

    # pylint: disable=too-many-instance-attributes,too-few-public-methods

    def __init__(self, state_file_path):
        self.state_file_path = os.path.expanduser(state_file_path)
        self.comments = self.comments_count = self.deviations = None
        self.deviations_count = self.replies = self.replies_count = None
        self.unread_notes = self.unread_notes_count = None
        self.old_comments = self.old_comments_count = None
        self.old_deviations = self.old_deviations_count = None
        self.old_replies = self.old_replies_count = None
        self.old_unread_notes = self.old_unread_notes_count = None

        # Loading previous state
        self.__load_state()


    def __create_cache_directory(self):
        try:

            # Making sure cache directory exists
            cache_directory = os.path.dirname(self.state_file_path)
            if not os.path.exists(cache_directory):
                os.mkdir(cache_directory)

        except Exception as e:
            raise Exception('Unable to create this program\'s cache '
                            'directory (\'%s\'):\n\n%s\n\n%s\n'
                            % (cache_directory, e, traceback.format_exc()))


    def __load_state(self):

        # Making sure cache directory is present
        self.__create_cache_directory()

        # Loading state if it exists
        if os.path.exists(self.state_file_path):

            # Loading YAML document
            try:
                with io.open(self.state_file_path, 'r') as state_file:
                    state = yaml.load(state_file, yaml.CLoader)
                if state is None:
                    raise Exception('YAML document empty')
            except Exception as e:
                raise Exception('Unable to load state from YAML document '
                                '(\'%s\'):\n\n%s\n\n%s\n'
                                % (self.state_file_path, e,
                                   traceback.format_exc()))

            # Configuring state
            self.comments = state.get('comments', [])
            self.comments_count = state.get('commentsCount', 0)
            self.deviations = state.get('deviations', [])
            self.deviations_count = state.get('deviationsCount', 0)
            self.replies = state.get('replies', [])
            self.replies_count = state.get('repliesCount', 0)
            self.unread_notes = state.get('unread_notes', [])
            self.unread_notes_count = state.get('unread_notesCount', 0)

        else:

            # Noting the fact no state is present
            print('No previous state to load - all counts set to 0')

            # Configuring service
            self.comments = []
            self.comments_count = 0
            self.deviations = []
            self.deviations_count = 0
            self.replies = []
            self.replies_count = 0
            self.unread_notes = []
            self.unread_notes_count = 0


    def save_state(self):
        '''Save internal state to the configured state file'''

        # Making sure cache directory is present
        self.__create_cache_directory()

        # Saving state file
        try:
            state = {'comments': self.comments,
                     'commentsCount': self.comments_count,
                     'deviations': self.deviations,
                     'deviationsCount': self.deviations_count,
                     'replies': self.replies,
                     'repliesCount': self.replies_count,
                     'unread_notes': self.unread_notes,
                     'unread_notesCount': self.unread_notes_count}
            with io.open(self.state_file_path, 'w') as state_file:
                yaml.dump(state, state_file, yaml.CDumper)
        except Exception as e:
            raise Exception('Unable to save state into YAML document '
                            '(\'%s\'):\n\n%s\n\n%s\n'
                            % (self.state_file_path, e, traceback.format_exc()))


# Getting new-style class
class DeviantArtService(object):
    '''Access the deviantART webservice'''

    # pylint: disable=too-many-instance-attributes

    def __init__(self, username, password):
        self.__difi_url = 'https://www.deviantart.com/global/difi.php'
        self.__inbox_id = None
        self.__username = username
        self.__password = password
        self.__r = self.__s = None
        self.__last_content = None
        self.logged_in = False


    def __fetch_inbox_id(self):

        # Obtain inbox folder ID from message center
        try:
            difi_url = 'https://www.deviantart.com/global/difi.php'
            payload = {'c[]': 'MessageCenter;get_folders',
                       't': 'json'}
            self.__r = self.__s.post(difi_url, params=payload, timeout=60)
            self.__r.raise_for_status()
        except Exception as e:
            raise Exception('Unable to get inbox folder ID:\n\n%s\n\n%s\n'
                            % (e, traceback.format_exc()))

        # Making sure difi response is valid
        response = self.__r.json()
        if not validate_difi_response(response, 0):
            raise Exception('The DiFi page request for the inbox folder ID '
                            'succeeded but the DiFi request failed:\n\n%s\n'
                            % response)

        # Searching for first folder labeled as the inbox
        for folder in response['DiFi']['response']['calls'][0]['response']['content']:  # pylint: disable=line-too-long
            if folder['is_inbox']:
                self.__inbox_id = folder['folderid']
                break

        # Erroring if the inbox has not been found
        if self.__inbox_id is None:
            raise Exception('Unable to find inbox folder in Message Center '
                            'folders:\n\n%s\n' % response)


    def get_messages(self, state):
        '''Fetch new messages from deviantART'''

        # Ensure I am logged in first
        if not self.logged_in:
            raise Exception('Please login before calling get_messages')

        # Making sure inbox ID is known first
        if self.__inbox_id is None:
            self.__fetch_inbox_id()

        # Fetch relevant unread notes, deviations etc
        try:

            # 100 is a limit for number of things returned - real limit is
            # >101 and <150
            payload = {'c[]': ['MessageCenter;get_views;' +
                               str(self.__inbox_id) + ',oq:fb_comments:0:100:f',
                               'MessageCenter;get_views;' +
                               str(self.__inbox_id) + ',oq:fb_replies:0:100:f',
                               'MessageCenter;get_views;' +
                               str(self.__inbox_id) + ',oq:notes_unread:0:100:f',  # pylint: disable=line-too-long
                               'MessageCenter;get_views;' +
                               str(self.__inbox_id) + ',oq:devwatch:0:100:f:'
                               'tg=deviations'],
                       't': 'json'}
            self.__r = self.__s.post(self.__difi_url, params=payload,
                                     timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to get number of unread notes, deviations'
                            ' etc:\n\n%s\n\n%s\n' % (e,
                                                     traceback.format_exc()))

        # Making sure difi response and all contained calls are valid
        response = self.__r.json()
        if not validate_difi_response(response, range(5)):
            raise Exception('The DiFi page request to get number of unread '
                            'notes, deviations etc succeeded but the DiFi '
                            'request failed:\n\n%s\n' % response)

        # Copying current messages state to 'old' fields
        state.old_comments = state.comments[:]
        state.old_comments_count = state.comments_count
        state.old_replies = state.replies[:]
        state.old_replies_count = state.replies_count
        state.old_unread_notes = state.unread_notes[:]
        state.old_unread_notes_count = state.unread_notes_count
        state.old_deviations = state.deviations[:]
        state.old_deviations_count = state.deviations_count

        # Fetching and saving new message state. Note that replies are
        # basically comments so the class is reused
        state.comments = [Comment(int(hit['msgid']),
                                 extract_text(hit['title'], True),
                                 extract_text(hit['who'], True),
                                 int(hit['ts']), hit['url'],
                                 extract_text(hit['body']))
                         for hit in response['DiFi']['response']['calls'][COMMENTS]['response']['content'][0]['result']['hits']]  # pylint: disable=line-too-long
        state.comments_count = response['DiFi']['response']['calls'][COMMENTS]['response']['content'][0]['result']['count']  # pylint: disable=line-too-long
        state.replies = [Comment(int(hit['msgid']),
                                extract_text(hit['title'], True),
                                extract_text(hit['who'], True),
                                int(hit['ts']), hit['url'],
                                extract_text(hit['body']))
                        for hit in response['DiFi']['response']['calls'][REPLIES]['response']['content'][0]['result']['hits']]  # pylint: disable=line-too-long
        state.replies_count = response['DiFi']['response']['calls'][REPLIES]['response']['content'][0]['result']['count']  # pylint: disable=line-too-long

        state.unread_notes = [Note(int(hit['msgid']),
                                  extract_text(hit['title'], True),
                                  extract_text(hit['who'], True),
                                  int(hit['ts']))
                             for hit in response['DiFi']['response']['calls'][UNREAD_NOTES]['response']['content'][0]['result']['hits']]  # pylint: disable=line-too-long
        state.unread_notes_count = response['DiFi']['response']['calls'][UNREAD_NOTES]['response']['content'][0]['result']['count']  # pylint: disable=line-too-long
        state.deviations = [Deviation(hit['msgid'],
                                     extract_text(hit['title'], True),
                                     int(hit['ts']), hit['url'],
                                     extract_text(hit['username'], True))
                           for hit in response['DiFi']['response']['calls'][DEVIATIONS]['response']['content'][0]['result']['hits']]  # pylint: disable=line-too-long
        state.deviations_count = response['DiFi']['response']['calls'][DEVIATIONS]['response']['content'][0]['result']['count']  # pylint: disable=line-too-long
        state.save_state()


#     def get_messages_and_notes_counts(self):
#
#         # Attempting to locate messages table cell
#         messagesCell = self.__last_content.find('td', id='oh-menu-split')
#         if messagesCell is None:
#             raise Exception('Unable to find messages cell on deviantART main '
#                             'page')
#
#         # Extracting messages and notes count
#         spans = messagesCell.findAll('span')
#         if len(spans) < 2:
#             raise Exception('Unable to find messages and/or notes count in '
#                             'messages cell on deviantART main page:\n\n%s\n'
#                             % messagesCell)
#         try:
#             messagesCount = int(spans[0].text)
#             notesCount = int(spans[1].text)
#         except Exception as e:
#             raise Exception('Unable to parse messages and/or notes count on '
#                             'deviantART main page - not valid numbers?\n\n'
#                             'spans: %s\n\n%s\n\n%s\n' %
#                             (spans, e, traceback.format_exc()))
#
#         # Returning counts
#         return (messagesCount, notesCount)


    def get_note_folders(self):
        '''Obtain list of note folders from deviantART'''

        # DiFi doesn't appear to provide a way to get a list of folders, so just
        # fetching and parsing the notes page
        try:
            notifications_url = 'http://www.deviantart.com/notifications/notes'
            self.__r = self.__s.get(notifications_url, timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to load deviantART notes page:\n\n%s\n\n%s'
                            '\n' % (e, traceback.format_exc()))

        # Parsing page
        self.__last_content = bs4.BeautifulSoup(self.__r.content)

        # Determining list of note folders
        note_folders = []
        for folder_link in self.__last_content.select('a.folder-link'):

            # Validating link data
            if 'data-folderid' not in folder_link.attrs:
                raise Exception('Unable to obtain the folder ID from link tag '
                                '\'%s\' - failed to generate a list of notes '
                                'folders in the get_notes_folders call!'
                                % folder_link)
            if 'title' not in folder_link.attrs:
                raise Exception('Unable to obtain the folder title from link tag'
                                '\'%s\' - failed to generate a list of notes '
                                'folders in the get_notes_folders call!'
                                % folder_link)

            # 'rel' is actually the count of contained notes, used as a
            # sanity check . Note that even though there is only one rel attribute,
            # Beautiful Soup returns a list?? Also need to remove thousands
            # separator etc
            if 'rel' not in folder_link.attrs:
                raise Exception('Unable to obtain the folder notes count from '
                                'link tag \'%s\' - failed to generate a list of '
                                'notes folders in the get_notes_folders call!'
                                % folder_link)
            notes_count = int(folder_link.attrs['rel'][0].replace(',', ''))

            note_folder = NoteFolder(folder_link.attrs['data-folderid'],
                                           folder_link.attrs['title'])
            note_folder.site_note_count = notes_count
            note_folders.append(note_folder)

        return note_folders


    def get_note_in_folder(self, folder_ID, note_ID):
        '''Fetch a note from a folder'''

        # pylint: disable=too-many-branches,too-many-locals

        # Dealing with special folder_IDs - remember not to update the folder_ID
        # variable so that you don't permanently corrupt it
        prepared_folder_ID = format_note_folder_id(folder_ID)

        try:

            # The 'ui' field in the form is actually the 'userinfo' cookie -
            # its not directly usable via the cookie value, have to urldecode.
            # This is on top of having the correct login cookies...
            data = {'c[]': ['"Notes","display_note",[%s,%s]'
                           % (prepared_folder_ID, note_ID)],
                     'ui': urllib.parse.unquote(self.__s.cookies['userinfo']),
                     't': 'json'}
            self.__r = self.__s.post(self.__difi_url, data=data, timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to fetch note ID \'%s\' from folder ID '
                            '\'%s\':\n\n%s\n\n%s\n'
                            % (note_ID, folder_ID, e,
                               traceback.format_exc()))

        # Making sure difi response and all contained calls are valid
        response = self.__r.json()
        if not validate_difi_response(response, range(1)):
            raise Exception('The DiFi page request to fetch note ID \'%s\' from'
                            ' folder ID \'%s\' succeeded but the DiFi request '
                            'failed:\n\n%s\n'
                            % (note_ID, folder_ID, response))

        # Actual note data is returned in HTML
        html_data = bs4.BeautifulSoup(response['DiFi']['response']['calls'][0]['response']['content']['body'])  # pylint: disable=line-too-long

        # Fetching note title and validating
        note_span = html_data.select_one('span.mcb-title')
        if not note_span:
            raise Exception('Unable to obtain note title from the following note'
                            ' HTML:\n\n%s\n\nProblem occurred while fetching '
                            'note ID \'%s\' from folder ID \'%s\''
                            % (html_data.text, note_ID, folder_ID))
        note_title = note_span.text

        # Fetching sender details and validating
        sender_span = html_data.select_one('span.mcb-from')
        if not sender_span:
            raise Exception('Unable to obtain note sender from the following '
                            'note HTML:\n\n%s\n\nProblem occurred while fetching'
                            ' note ID \'%s\' from folder ID \'%s\''
                            % (html_data.text, note_ID, folder_ID))
        if 'username' not in sender_span.attrs:
            raise Exception('Unable to obtain note sender username from the '
                            'following note HTML:\n\n%s\n\nProblem occurred '
                            'while fetching note ID \'%s\' from folder ID \'%s\''
                            % (html_data.text, note_ID, folder_ID))
        note_sender = sender_span.attrs['username']

        # Fetching timestamp and validating
        timestamp_span = html_data.select_one('span.mcb-ts')
        if not timestamp_span:
            raise Exception('Unable to obtain timestamp span from the '
                            'following note HTML:\n\n%s\n\nProblem occurred'
                            ' while fetching note ID \'%s\' from folder ID \'%s\''
                            % (html_data.text, note_ID, folder_ID))
        if 'title' not in timestamp_span.attrs:
            raise Exception('Unable to obtain timestamp \'title\' from the '
                            'timestamp span from the following note HTML:'
                            '\n\n%s\n\nProblem occurred while fetching note ID '
                            '\'%s\' from folder ID \'%s\''
                            % (html_data.text, note_ID, folder_ID))
        note_timestamp = timestamp_span.attrs['title']

        # If the timestamp includes 'ago', its not the proper timestamp - after
        # notes get ~1 week old, deviantART switches the proper timestamp into
        # the tag text rather than the title attribute
        if 'ago' in note_timestamp:
            note_timestamp = timestamp_span.text

        try:

            # Converting the deviantART datetime string into a proper UNIX
            # timestamp
            # Example: 'Jun 9, 2014, 11:08:28 PM'
            note_timestamp = datetime.datetime.strptime(note_timestamp,
                                                '%b %d, %Y, %I:%M:%S %p')
            note_timestamp = note_timestamp.timestamp()

        except ValueError as e:
            raise Exception('Unable to parse timestamp \'%s\' from note ID '
                            '\'%s\' while fetching note from folder ID \'%s\':'
                            '\n\n%s\n\n%s\n'
                            % (note_timestamp, note_ID, folder_ID, e,
                               traceback.format_exc()))

        # Fetching note HTML and validating
        div_wraptext = html_data.select_one('.mcb-body.wrap-text')
        if not div_wraptext:
            raise Exception('Unable to parse note text from the following note '
                            'HTML:\n\n%s\n\nProblem occurred while '
                            'fetching note ID \'%s\' from from folder ID \'%s\''
                            % (html_data, note_ID, folder_ID))

        # Turn links in the div to text links without the deviantART redirector
        for link_tag in div_wraptext.select('a'):
            if 'http://' in link_tag.attrs['href']:
                link_tag.string = link_tag.attrs['href'].replace(
                            'http://www.deviantart.com/users/outgoing?', '')
            else:
                link_tag.string = link_tag.attrs['href'].replace(
                            'https://www.deviantart.com/users/outgoing?', '')
            link_tag.unwrap()

        # Replace out linebreaks with newlines to ensure they get honoured
        for linebreak in div_wraptext.select('br'):
            linebreak.replace_with('\n')

        # TODO: In the future I should textify things like smilies etc
        note_text = div_wraptext.text.strip()

        # Finally instantiating the note
        note = Note(note_ID, note_title, note_sender, note_timestamp)
        note.text = note_text
        note.folder_ID = folder_ID

        return note


    def get_note_ids_in_folder(self, folder_ID):
        '''Fetch all note IDs in a set from specified folder (one DiFi call per
        25 notes) - used to audit note IDs stored in the database'''

        # Dealing with special folder_IDs
        folder_ID = format_note_folder_id(folder_ID)

        note_ids = set()
        offset = 0
        while True:

            notes_detected = False

            try:

                # The 'ui' field in the form is actually the 'userinfo' cookie -
                # its not directly usable via the cookie value, have to urldecode.
                # This is on top of having the correct login cookies...
                data = {'c[]': ['"Notes","display_folder",[%s,%s,0]'
                               % (folder_ID, offset)],
                         'ui': urllib.parse.unquote(self.__s.cookies['userinfo']),
                         't': 'json'}
                self.__r = self.__s.post(self.__difi_url, data=data, timeout=60)
                self.__r.raise_for_status()

            except Exception as e:
                raise Exception('Unable to fetch note IDs from offset \'%s\' '
                                'from folder ID \'%s\':\n\n%s\n\n%s\n'
                                % (offset, folder_ID, e,
                                   traceback.format_exc()))

            # Making sure difi response and all contained calls are valid
            response = self.__r.json()
            if not validate_difi_response(response, range(1)):
                raise Exception('The DiFi page request to fetch note IDs from '
                                'offset \'%s\' from folder ID \'%s\' succeeded '
                                'but the DiFi request failed:\n\n%s\n'
                                % (offset, folder_ID, response))

            # Actual note data is returned in HTML
            html_data = bs4.BeautifulSoup(response['DiFi']['response']['calls'][0]['response']['content']['body'])  # pylint: disable=line-too-long

            for listitem_tag in html_data.select('li.note'):

                notes_detected = True

                # Fetching note details and validating
                note_details = listitem_tag.select_one('.note-details')
                if not note_details:
                    raise Exception('Unable to parse note details from the '
                                    'following note HTML:\n\n%s\n\nProblem '
                                    'occurred while fetching note IDs from '
                                    'offset \'%s\' from folder ID \'%s\''
                                    % (listitem_tag, offset, folder_ID))

                # Fetching note ID and validating
                note_details_link = note_details.select_one('span > a')
                if not note_details_link:
                    raise Exception('Unable to parse note details link from the '
                                    ' following note HTML:\n\n%s\n\nProblem '
                                    'occurred while fetching note IDs from '
                                    'offset \'%s\' from folder ID \'%s\''
                                    % (listitem_tag, offset, folder_ID))
                if 'data-noteid' not in note_details_link.attrs:
                    raise Exception('Unable to obtain note ID from note details '
                                    'link from the following note HTML:\n\n%s'
                                    '\n\nProblem occurred while fetching notes '
                                    'from offset \'%s\' from folder ID \'%s\''
                                    % (listitem_tag, offset, folder_ID))

                # Collecting obtained note_ID - note that these IDs are supposed
                # to be ints, affects set comparisons etc (int !=str)
                note_ids.add(int(note_details_link.attrs['data-noteid']))

            # Breaking if no notes were returned
            if not notes_detected:
                break

            # Looping - notes are available in 25-note pages
            offset += 25

        return note_ids


    def get_notes_in_folder(self, folder_ID, note_offset):
        '''Fetch desired notes from specified folder, with the offset allowing
        you to page through the folder (max 25 notes are returned by
        deviantART), note data is fetched in separate DiFi calls'''

        # Dealing with special folder_IDs - remember not to update the folder_ID
        # variable so that you don't permanently corrupt it
        prepared_folder_ID = format_note_folder_id(folder_ID)

        try:

            # The 'ui' field in the form is actually the 'userinfo' cookie -
            # its not directly usable via the cookie value, have to urldecode.
            # This is on top of having the correct login cookies...
            data = {'c[]': ['"Notes","display_folder",[%s,%s,0]'
                           % (prepared_folder_ID, note_offset)],
                     'ui': urllib.parse.unquote(self.__s.cookies['userinfo']),
                     't': 'json'}
            self.__r = self.__s.post(self.__difi_url, data=data, timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to fetch notes from offset \'%s\' from '
                            'folder ID \'%s\':\n\n%s\n\n%s\n'
                            % (note_offset, folder_ID, e,
                               traceback.format_exc()))

        # Making sure difi response and all contained calls are valid
        response = self.__r.json()
        if not validate_difi_response(response, range(1)):
            raise Exception('The DiFi page request to fetch notes from offset '
                            '\'%s\' from folder ID \'%s\' succeeded but the DiFi'
                            ' request failed:\n\n%s\n'
                            % (note_offset, folder_ID, response))

        # Actual note data is returned in HTML - note that this is actually a
        # preview (corrupted URLs and linebreaks), so notes must still be fetched
        # individually
        html_data = bs4.BeautifulSoup(response['DiFi']['response']['calls'][0]['response']['content']['body'])  # pylint: disable=line-too-long

        notes = []
        for listitem_tag in html_data.select('li.note'):

            # Fetching note details and validating
            note_details = listitem_tag.select_one('.note-details')
            if not note_details:
                raise Exception('Unable to parse note details from the following'
                                ' note HTML:\n\n%s\n\nProblem occurred while '
                                'fetching notes from offset \'%s\' from folder '
                                'ID \'%s\'' % (listitem_tag, note_offset,
                                               folder_ID))

            # Fetching note ID and validating
            note_details_link = note_details.select_one('span > a')
            if not note_details_link:
                raise Exception('Unable to parse note details link from the '
                                ' following note HTML:\n\n%s\n\nProblem occurred'
                                ' while fetching notes from offset \'%s\' from '
                                'folder ID \'%s\''
                                % (listitem_tag, note_offset, folder_ID))
            if 'data-noteid' not in note_details_link.attrs:
                raise Exception('Unable to obtain note ID from note details link'
                                ' from the following note HTML:\n\n%s\n\nProblem'
                                ' occurred while fetching notes from offset '
                                '\'%s\' from folder ID \'%s\''
                                % (listitem_tag, note_offset, folder_ID))
            note_ID = note_details_link.attrs['data-noteid']

            # Fetching the note text and metadata separately - it turns out that
            # at this level you really do just get a preview, which has corrupted
            # links and collapsed newlines
            notes.append(self.get_note_in_folder(folder_ID, note_ID))

        return notes


    def last_page_content(self):
        '''The last normal page loaded by requests'''

        return self.__last_content


    def login(self):
        '''Login to deviantART'''

        # You need to fetch the login page first as the login form contains some
        # dynamic hidden fields. Using a Session object persists cookies and
        # maintains Keep-Alive
        try:
            login_url = 'https://www.deviantart.com/users/login'
            self.__s = requests.Session()
            self.__r = self.__s.get(login_url, timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to load deviantART login page:\n\n%s\n\n%s'
                            '\n' % (e, traceback.format_exc()))

        # Parsing page
        self.__last_content = bs4.BeautifulSoup(self.__r.content)

        # Locating login form
        login_form = self.__last_content.find('form', id='form-login')
        if login_form is None:
            raise Exception('Unable to find login form on deviantART login'
                            ' page')

        # Obtaining hidden validation fields
        try:
            validate_token = login_form.find('input',
                                             attrs={'name': 'validate_token'}).get('value')  # pylint: disable=line-too-long
            validate_key = login_form.find('input',
                                           attrs={'name': 'validate_key'}).get('value')  # pylint: disable=line-too-long
        except Exception as e:
            raise Exception('Unable to fetch hidden validation field values in '
                            'deviantART\'s login form:\n\n%s\n\n%s\n'
                            % (e, traceback.format_exc()))

        # Debug code
        #print('validate_token: %s\nvalidate_key: %s'% (validate_token,
        #                                               validate_key))

        # Logging in to deviantART - this gets me the cookies that I can then
        # use elsewhere
        try:
            payload = {'username': self.__username,
                       'password': self.__password,
                       'validate_token': validate_token,
                       'validate_key': validate_key,
                       'remember_me': 1}
            self.__r = self.__s.post(login_url, data=payload, timeout=60)
            self.__r.raise_for_status()

        except Exception as e:
            raise Exception('Unable to POST to deviantART login page:\n\n%s\n'
                            '\n%s\n' % (e, traceback.format_exc()))

        # Recording the fact we have now successfully logged in
        self.logged_in = True

        # Updating recorded page content
        self.__last_content = bs4.BeautifulSoup(self.__r.content)


class Comment:
    '''Represents a comment or reply (the latter is basically a comment. Replies
    are called 'Feedback Messages' on deviantART'''

    # pylint: disable=too-few-public-methods, too-many-arguments

    def __init__(self, ID, title, who, ts, URL, body):

        self.ID = ID
        self.title = title  # This is a description of the page the comment is
                            # on
        self.who = who
        self.ts = ts
        self.URL = URL
        self.body = body

    def __hash__(self, *args, **kwargs):

        # Defining hashable interface based on ID so that the object can be
        # used in a set
        return self.ID

    def __eq__(self, other):

        # Required comparison operations for set membership etc
        return hash(self) == hash(other)

    def __neq__(self, other):

        # Required comparison operations for set membership etc
        return not self.__eq__(other)


class Deviation:
    '''Represents a deviation'''

    # pylint: disable=too-few-public-methods

    def __init__(self, ID, title, ts, URL, username):  # pylint: disable=too-many-arguments

        self.ID = ID
        self.title = title
        self.ts = ts
        self.URL = URL
        self.username = username

    def __hash__(self, *args, **kwargs):

        # Defining hashable interface based on ID so that the object can be
        # used in a set. __hash__ must return an integer, however the
        # deviantART IDs seem to be a string in the form <number>:<number>
        return int(self.ID.split(':')[1])

    def __eq__(self, other):

        # Required comparison operations for set membership etc
        return hash(self) == hash(other)

    def __neq__(self, other):

        # Required comparison operations for set membership etc
        return not self.__eq__(other)


class Note:
    '''Represents a note'''

    # pylint: disable=too-few-public-methods

    # Notes have normally been populated via the MessageCenter view, which
    # doesn't include the note text - however this is now available
    # Rather than a Note, this is more a 'note view', since one Note can be in
    # more than one NoteFolder (e.g. Inbox and Starred) - however I want to keep
    # things simple currently and stick with one folder ID per Note object
    # TODO: Make note text and folder_ID initialised immediately
    def __init__(self, ID, title, who, ts):

        # Making sure ID is an int if it is passed in as a string (this is
        # relied on for comparisons, the ID increments over time)
        if isinstance(ID, str):
            if not ID.isdigit():
                raise Exception('Unable to instantiate note with non integer ID'
                                ' \'%s\'!' % ID)
            ID = int(ID)

        self.ID = ID
        self.title = title
        self.who = who
        self.ts = ts
        self.text = None
        self.folder_ID = None


    def __hash__(self, *args, **kwargs):

        # Defining hashable interface based on ID so that the object can be
        # used in a set
        return self.ID

    def __eq__(self, other):

        # Required comparison operations for set membership etc
        return hash(self) == hash(other)

    def __neq__(self, other):

        # Required comparison operations for set membership etc
        return not self.__eq__(other)


class NoteFolder:
    '''Represents a default or custom folder for notes (in reality a view on
    applicable notes in deviantART'''

    # pylint: disable=too-few-public-methods

    def __init__(self, ID, title):

        # ID is actually text, can be actual strings like 'unread'
        self.ID = ID
        self.title = title

        # Useful stat to use as a heuristic for unnoticed change detection
        self.site_note_count = None

    def __hash__(self, *args, **kwargs):

        # Defining hashable interface based on ID so that the object can be
        # used in a set
        return self.ID

    def __eq__(self, other):

        # Required comparison operations for set membership etc
        return hash(self) == hash(other)

    def __neq__(self, other):

        # Required comparison operations for set membership etc
        return not self.__eq__(other)


def extract_text(html_text, collapse_lines=False):
    '''Extract lines of text from HTML tags - this honours linebreaks'''

    # Strings is a generator
    # Cope with html_text when it is already a BeautifulSoup tag
    if isinstance(html_text, str):
        html_text = bs4.BeautifulSoup(html_text)
    text = '\n'.join(html_text.strings)
    return text if not collapse_lines else text.replace('\n', ' ')


def format_note_folder_id(folder_ID):
    '''Dealing with special folder_IDs that are genuinely strings (e.g.
    unread') - these need to be speechmark-delimited for deviantART not to
    raise a bullshit error about the class name'''

    if not folder_ID.isdigit():
        return '"%s"' % folder_ID
    else:
        return folder_ID


def get_new(state, messages_type):
    '''Determining what new messages have been fetched'''

    # Dealing with different message types requested
    if messages_type == COMMENTS:
        return set(state.comments) - set(state.old_comments)
    elif messages_type == REPLIES:
        return set(state.replies) - set(state.old_replies)
    elif messages_type == UNREAD_NOTES:
        return set(state.unread_notes) - set(state.old_unread_notes)
    elif messages_type == DEVIATIONS:
        return set(state.deviations) - set(state.old_deviations)
    else:

        # Invalid messages_type passed
        raise Exception('get_new was called with an invalid messages_type'
                        ' (%s)' % messages_type)


def validate_difi_response(response, call_numbers):
    '''Determining if the overall DiFi page call and all associated function
    calls were successful or not'''

    # Making sure call_numbers is iterable - e.g. just one call number was
    # passed
    if not isinstance(call_numbers, collections.Sequence):
        call_numbers = [call_numbers]

    # Failing if overall call failed
    if response['DiFi']['status'] != 'SUCCESS':
        return False

    # Failing if any one call failed
    for call_number in call_numbers:
        if response['DiFi']['response']['calls'][call_number]['response']['status'] != 'SUCCESS':  # pylint: disable=line-too-long
            return False

    return True