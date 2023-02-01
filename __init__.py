# -*- coding: utf-8 -*-

"""
X11 window switcher - list and activate windows.
to enable search on all workspaces start query with *
performs tokenized search-> order doesnt matter
fuzzy search can be enabled in settings
"""

import subprocess
from collections import namedtuple
from collections import defaultdict
from functools import reduce
#from fuzzywuzzy import fuzz #https://chairnerd.seatgeek.com/fuzzywuzzy-fuzzy-string-matching-in-python/
import re

from albert import Item, Action, QueryHandler, runDetachedProcess
from albert import debug as debug_orig

actions = {
    'switch': {
        'text': 'Switch Window',
        'args': '-a'
    },
    'move': {
        'text': 'Move window to this desktop',
        'args': '-R'
    },
    'close': {
        'text': 'Close the window gracefully',
        'args': '-c'
    }
}
Window = namedtuple("Window", ["wid", "desktop", "wm_class", "host", "wm_name"])
# search algo inspired by https://github.com/daniellandau/switcher/blob/master/util.js

### Settings###
matchFuzzy = False
orderByRelevancy = True
DEBUG=False

md_iid = "0.5"
md_version = "0.5"
md_id = "window-switcher-plus"
md_name = "Window Switcher Plus"
md_description = "advanced window switcher plugin for albert launcher"
md_url = "https://github.com/VietTralala/window-switcher-plus"
# md_maintainers = ["Ed Perez", "Manuel Schneider", "dshoreman"]
md_maintainers = ["Viet Tran", "seiuneko"]
md_bin_dependencies = ["wmctrl"]


def getWindows():
    windows = []
    for line in subprocess.check_output(['wmctrl', '-l', '-x']).splitlines():
        win = Window(*[token.decode() for token in line.split(None,4)])
        if win.desktop != "-1":
            windows.append(win)
    
    return windows

def createActions(wid):
    for action, action_data in actions.items():
        yield Action(id=action,
                     text=action_data['text'],
                     callable=lambda x=action_data['args']: runDetachedProcess(['wmctrl', '-i', x, wid]))

def createItems(windows, spans=None):
    results = []
    # print('spans: ', spans)
    # [print(w.wm_name) for w in windows]
    # print(len(windows))
    for win in windows:
        text_subtext = {'text':'%s: %s' % (win.desktop, win.wm_class.split('.')[-1].replace('-',' ')),
                        'subtext':'%s➜%s' %(win.wm_class.split('.')[-1], win.wm_name)}

        results.append(Item(id="%s%s" % (md_name, win.wm_class),
                            **text_subtext,
                            icon=[],
                            completion='',
                            actions=list(createActions(win.wid)),
                            ))


    return results

def highlightText(win, spans):
    '''
    input: 
    spans: list(tuple(int, int)) - describing match positions
    '''

    # sort spans
    spans.sort(key=lambda x: x[0])
    
    # check spans not overlapping, this could be problem when doing fuzzy search


    text='%s: %s' % (win.desktop, win.wm_class.split('.')[-1].replace('-',' '))
    subtext = ''

    description = '%s➜%s' %(win.wm_class.split('.')[-1], win.wm_name)
    len_wm_class = len(win.wm_class.split('.')[-1].lower())

    last_pos = 0

    for s_init, s_end in spans:
        before = description[last_pos:s_init]
        highlight = description[s_init:s_end]

        subtext+='%s<u>%s</u>'% (before, highlight)
        last_pos = s_end

    subtext += description[last_pos:]
    return {'text':text, 'subtext':subtext }


def getCurrentWorkspace():
    for line in subprocess.check_output(['wmctrl', '-d']).splitlines():
        cols = line.split()
        if cols[1].decode() == '*':
            return cols[0].decode()
    
    return None

def filterWindows(query, curWS, windows):
    ''' if query starts with *, do search on all workspaces

    returns:
    windows: list[Window(=namedtuple)]
    spans: dict(window_id:[(match_0_start, match_0_end),...,(match_N_start, match_N_end) ] )
    '''
    if not query or not curWS or not windows:
        return [w for w in windows if w.desktop == curWS], None

    query =  query.split()
    if query[0].startswith('*'):
        query[0] = query[0].strip('*')
        if query[0] == '': # inserted space after *
            del query[0]
            if len(query)==0:
                return windows, None

    else:
        windows = [w for w in windows if w.desktop == curWS]
    
    # for every window run every query token, add score
    scores = defaultdict(int)
    spans = defaultdict(list)
    for win in windows:
        wm_class = win.wm_class.split('.')[-1].lower()
        wm_name = win.wm_name
        descriptionLowerCase = '%s %s' %(wm_class, wm_name)
        score = 0
        
        for query_token in query:
            # print('search for  <%s> in <%s>'%(query_token,descriptionLowerCase))
            score, matchPos =  calculateScore(descriptionLowerCase, query_token)
            scores[win.wid] += score
            spans[win.wid].extend(matchPos)

            
        # print('score:', scores[win.wid])

    # remove score zero windows and sort
    windows = [w for w in windows if scores[w.wid] != 0 ]
    if orderByRelevancy:
        windows.sort(key=lambda x: scores[x.wid], reverse=True)
    else:
        windows.sort(key=lambda x: '%s %s' %(x.wm_class.split('.')[-1], x.wm_name), reverse=True)
    spans = { wid:sp for wid, sp in spans.items() if scores[wid] != 0 }
    # [print(w.wm_name, scores[w.wid]) for w in windows]
    return windows, spans
    


    
def calculateScore(description, query_token):
    if query_token == '':
        return True
    regexp = createRegExp(query_token)
    # print(regex)

    score = 0
    gotMatch = False
    spans = []

    for match in re.finditer(regexp, description,flags=re.I):
        # print('%s -> matches: %s'%(regexp, match))
        # A full match at the beginning is the best match
        if (match.start() == 0 and len(match.group(0)) == len(query_token)):
            score += 100
        
         # matches at beginning word boundaries are better than in the middle of words
        if( match.start() == 0 or (match.start != 0 and description[match.start() - 1] == ' ')):
            wordPrefixFactor = 1.2
        else:
            wordPrefixFactor = 0.0

        # matches nearer to the beginning are better than near the end
        precedenceFactor = 1.0 / (1 + match.start())

        # fuzzyness can cause lots of stuff to match, penalize by match length
        fuzzynessFactor = (2.3 * (len(query_token) - len(match.group(0))  )) / len(match.group(0))

        # join factors by summing
        newscore = precedenceFactor + wordPrefixFactor + fuzzynessFactor
        score = max(score, newscore)
        spans.append(match.span())
        gotMatch = True
    return score, spans

def debug(msg):
    if DEBUG:
        W  = '\033[0m'  # white (normal)
        R  = '\033[31m' # red
        debug_orig(f"{R}{md_name}:{W} {msg}")


def createRegExp(query_token):
    regex = ''
    if matchFuzzy:
        query_token = [re.escape(x) for x in list(query_token)]
    else:
        query_token = [re.escape(query_token)]

    regex = reduce(lambda a,b: a + '[^' + b + ']*' + b, query_token)
    # print(regex)
    return regex
    

class Plugin(QueryHandler):
    def id(self):
        return md_id

    def name(self):
        return md_name

    def description(self):
        return md_description

    def defaultTrigger(self):
        return " "

    def initialize(self):
        debug('initialize')

    def finalize(self):
        debug('finalize')

    def handleQuery(self, query):
        stripped = query.string.strip().lower()

        if stripped:
            curWS =  getCurrentWorkspace()
            results = []
            windows = getWindows()

            debug(query.string)
            debug(windows)

            windows, matchPos = filterWindows(stripped, curWS, windows)
            results = createItems(windows, spans=matchPos)
            query.add(results)