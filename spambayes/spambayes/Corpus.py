#! /usr/bin/env python

'''Corpus.py - Spambayes corpus management framework.

Classes:
    Corpus - a collection of Messages
    ExpiryCorpus - a "young" Corpus
    Message - a subject of Spambayes training
    MessageFactory - creates a Message

Abstract:
    A corpus is defined as a set of messages that share some common
    characteristic relative to spamness.  Examples might be spam, ham,
    unsure, or untrained, or "bayes rating between .4 and .6.  A
    corpus is a collection of messages.  Corpus is a dictionary that
    is keyed by the keys of the messages within it.  It is iterable,
    and observable.  Observers are notified when a message is added
    to or removed from the corpus.

    Corpus is designed to cache message objects.  By default, it will
    only engage in lazy creation of message objects, keeping those
    objects in memory until the corpus instance itself is destroyed.
    In large corpora, this could consume a large amount of memory.  A
    cacheSize operand is implemented on the constructor, which is used
    to limit the *number* of messages currently loaded into memory.
    The instance variable that implements this cache is
    Corpus.Corpus.msgs, a dictionary.  Access to this variable should
    be through keys(), [key], or using an iterator.  Direct access
    should not be used, as subclasses that manage their cache may use
    this variable very differently.

    Iterating Corpus objects is potentially very expensive, as each
    message in the corpus will be brought into memory.  For large
    corpora, this could consume a lot of system resources.

    ExpiryCorpus is designed to keep a corpus of file messages that
    are guaranteed to be younger than a given age.  The age is
    specified on the constructor, as a number of seconds in the past.
    If a message file was created before that point in time, the a
    message is deemed to be "old" and thus ignored.  Access to a
    message that is deemed to be old will raise KeyError, which should
    be handled by the corpus user as appropriate.  While iterating,
    KeyError is handled by the iterator, and messages that raise
    KeyError are ignored.

    As messages pass their "expiration date," they are eligible for
    removal from the corpus. To remove them properly,
    removeExpiredMessages() should be called.  As messages are removed,
    observers are notified.

    ExpiryCorpus function is included into a concrete Corpus through
    multiple inheritance. It must be inherited before any inheritance
    that derives from Corpus.  For example:

        class RealCorpus(Corpus)
           ...

        class ExpiryRealCorpus(Corpus.ExpiryCorpus, RealCorpus)
           ...

    Messages have substance, which is is the textual content of the
    message. They also have a key, which uniquely defines them within
    the corpus.  This framework makes no assumptions about how or if
    messages persist.

    MessageFactory is a required factory class, because Corpus is
    designed to do lazy initialization of messages and as an abstract
    class, must know how to create concrete instances of the correct
    class.

To Do:
    o Suggestions?

    '''

# This module is part of the spambayes project, which is Copyright 2002
# The Python Software Foundation and is covered by the Python Software
# Foundation license.

__author__ = "Tim Stone <tim@fourstonesExpressions.com>"
__credits__ = "Richie Hindle, Tim Peters, all the spambayes contributors."

from __future__ import generators

try:
    True, False
except NameError:
    # Maintain compatibility with Python 2.2
    True, False = 1, 0
    def bool(val):
        return not not val

import sys           # for output of docstring
import time
import re
from spambayes import tokenizer
from spambayes.Options import options

SPAM = True
HAM = False

class Corpus:
    '''An observable dictionary of Messages'''

    def __init__(self, factory, cacheSize=-1):
        '''Constructor(MessageFactory)'''

        self.msgs = {}            # dict of all messages in corpus
                                  # value is None if msg not currently loaded
        self.keysInMemory = []    # keys of messages currently loaded
                                  # this *could* be derived by iterating msgs
        self.cacheSize = cacheSize  # max number of messages in memory
        self.observers = []       # observers of this corpus
        self.factory = factory    # factory for the correct Message subclass

    def addObserver(self, observer):
        '''Register an observer, which must implement
        onAddMessage, onRemoveMessage'''

        self.observers.append(observer)

    def addMessage(self, message):
        '''Add a Message to this corpus'''

        if options.verbose:
            print 'adding message %s to corpus' % (message.key())

        self.cacheMessage(message)

        for obs in self.observers:
            # there is no reason that a Corpus observer MUST be a Trainer
            # and so it may very well not be interested in AddMessage events
            # even though right now the only observable events are
            # training related
            try:
                obs.onAddMessage(message)
            except AttributeError:   # ignore if not implemented
                pass

    def removeMessage(self, message):
        '''Remove a Message from this corpus'''

        key = message.key()
        if options.verbose:
            print 'removing message %s from corpus' % (key)
        self.unCacheMessage(key)
        del self.msgs[key]

        for obs in self.observers:
            # see comments in event loop in addMessage
            try:
                obs.onRemoveMessage(message)
            except AttributeError:
                pass

    def cacheMessage(self, message):
        '''Add a message to the in-memory cache'''
        # This method should probably not be overridden

        key = message.key()

        if options.verbose:
            print 'placing %s in corpus cache' % (key)

        self.msgs[key] = message

        # Here is where we manage the in-memory cache size...
        self.keysInMemory.append(key)

        if self.cacheSize > 0:       # performance optimization
            if len(self.keysInMemory) > self.cacheSize:
                keyToFlush = self.keysInMemory[0]
                self.unCacheMessage(keyToFlush)

    def unCacheMessage(self, key):
        '''Remove a message from the in-memory cache'''
        # This method should probably not be overridden

        if options.verbose:
            print 'Flushing %s from corpus cache' % (key)

        try:
            ki = self.keysInMemory.index(key)
        except ValueError:
            pass
        else:
            del self.keysInMemory[ki]

        self.msgs[key] = None

    def takeMessage(self, key, fromcorpus):
        '''Move a Message from another corpus to this corpus'''

        # XXX Hack: Calling msg.getSubstance() here ensures that the
        # message substance is in memory.  If it isn't, when addMessage()
        # calls message.store(), which calls message.getSubstance(), that
        # will try to load the substance from the as-yet-unwritten new file.
        msg = fromcorpus[key]
        msg.getSubstance()
        fromcorpus.removeMessage(msg)
        self.addMessage(msg)

    def __getitem__(self, key):
        '''Corpus is a dictionary'''

        amsg = self.msgs[key]

        if not amsg:
            amsg = self.makeMessage(key)     # lazy init, saves memory
            self.cacheMessage(amsg)

        return amsg

    def keys(self):
        '''Message keys in the Corpus'''

        return self.msgs.keys()

    def __iter__(self):
        '''Corpus is iterable'''

        for key in self.keys():
            try:
                yield self[key]
            except KeyError:
                pass

    def __str__(self):
        '''Instance as a printable string'''

        return self.__repr__()

    def __repr__(self):
        '''Instance as a representative string'''

        raise NotImplementedError

    def makeMessage(self, key):
        '''Call the factory to make a message'''

        # This method will likely be overridden
        msg = self.factory.create(key)

        return msg


class ExpiryCorpus:
    '''Corpus of "young" file system artifacts'''

    def __init__(self, expireBefore):
        '''Constructor'''

        self.expireBefore = expireBefore

    def removeExpiredMessages(self):
        '''Kill expired messages'''

        for msg in self:
            if msg.createTimestamp() < time.time() - self.expireBefore:
                if options.verbose:
                    print 'message %s has expired' % (key)
                self.removeMessage(msg)


class Message:
    '''Abstract Message class'''

    def __init__(self):
        '''Constructor()'''

        # The text of the message headers and body are held in attributes
        # called 'hdrtxt' and 'payload', created on demand in __getattr__
        # by calling load(), which should in turn call setSubstance().
        # This means you don't need to remember to call load() before
        # using these attributes.

    def __getattr__(self, attributeName):
        '''On-demand loading of the message text.'''

        if attributeName in ('hdrtxt', 'payload'):
            self.load()
        return getattr(self, attributeName)

    def load(self):
        '''Method to load headers and body'''

        raise NotImplementedError

    def store(self):
        '''Method to persist a message'''

        raise NotImplementedError

    def remove(self):
        '''Method to obliterate a message'''

        raise NotImplementedError

    def __repr__(self):
        '''Instance as a representative string'''

        raise NotImplementedError

    def __str__(self):
        '''Instance as a printable string'''

        return self.getSubstance()

    def name(self):
        '''Message may have a unique human readable name'''

        return self.__repr__()

    def key(self):
        '''The key for this instance'''

        raise NotImplementedError

    def setSubstance(self, sub):
        '''set this message substance'''

        bodyRE = re.compile(r"\r?\n(\r?\n)(.*)", re.DOTALL+re.MULTILINE)
        bmatch = bodyRE.search(sub)
        if bmatch:
            self.payload = bmatch.group(2)
            self.hdrtxt = sub[:bmatch.start(2)]

    def getSubstance(self):
        '''Return this message substance'''

        return self.hdrtxt + self.payload

    def setSpamprob(self, prob):
        '''Score of the last spamprob calc, may not be persistent'''

        self.spamprob = prob

    def tokenize(self):
        '''Returns substance as tokens'''

        return tokenizer.tokenize(self.getSubstance())

    def createTimeStamp(self):
        '''Returns the create time of this message'''
        # Should return a timestamp like time.time()

        raise NotImplementedError

    def getFrom(self):
        '''Return a message From header content'''

        if self.hdrtxt:
            match = re.search(r'^From:(.*)$', self.hdrtxt, re.MULTILINE)
            return match.group(1)
        else:
            return None

    def getSubject(self):
        '''Return a message Subject header contents'''

        if self.hdrtxt:
            match = re.search(r'^Subject:(.*)$', self.hdrtxt, re.MULTILINE)
            return match.group(1)
        else:
            return None

    def getDate(self):
        '''Return a message Date header contents'''

        if self.hdrtxt:
            match = re.search(r'^Date:(.*)$', self.hdrtxt, re.MULTILINE)
            return match.group(1)
        else:
            return None

    def getHeadersList(self):
        '''Return a list of message header tuples'''

        hdrregex = re.compile(r'^([A-Za-z0-9-_]*): ?(.*)$', re.MULTILINE)
        data = re.sub(r'\r?\n\r?\s',' ',self.hdrtxt,re.MULTILINE)
        match = hdrregex.findall(data)

        return match

    def getHeaders(self):
        '''Return message headers as text'''

        return self.hdrtxt

    def getPayload(self):
        '''Return the message body'''

        return self.payload

    def stripSBDHeader(self):
        '''Removes the X-Spambayes-Disposition: header from the message'''

        # This is useful for training, where a spammer may be spoofing
        # our header, to make sure that our header doesn't become an
        # overweight clue to hamminess

        raise NotImplementedError


class MessageFactory:
    '''Abstract Message Factory'''

    def __init__(self):
        '''Constructor()'''
        pass

    def create(self, key):
        '''Create a message instance'''

        raise NotImplementedError


if __name__ == '__main__':
    print >>sys.stderr, __doc__