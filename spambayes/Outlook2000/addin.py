# SpamBayes Outlook Addin

import sys, os
import warnings
import traceback

try:
    True, False
except NameError:
    # Maintain compatibility with Python 2.2
    True, False = 1, 0

# We have lots of locale woes.  The short story:
# * Outlook/MAPI will change the locale on us as some predictable
#   times - but also at unpredictable times.
# * Python currently insists on "C" locale - if it isn't, subtle things break,
#   such as floating point constants loaded from .pyc files.
# * Our config files also want a consistent locale, so periods and commas
#   are the same when they are read as when they are written.
# So, at a few opportune times, we simple set it back.
# We do it here as early as possible, before any imports that may see this
#
# See also [725466] Include a proper locale fix in Options.py,
# assorted errors relating to strange math errors, and spambayes-dev archives,
# starting July 23 2003.
import locale
locale.setlocale(locale.LC_NUMERIC, "C")

if sys.version_info >= (2, 3):
    # sick off the new hex() warnings!
    # todo - remove this - win32all has removed all these warnings
    # (but we will wait some time for people to update)
    warnings.filterwarnings("ignore", category=FutureWarning, append=1)
    # Binary builds can avoid our pendingdeprecation too
    if hasattr(sys, "frozen"):
        warnings.filterwarnings("ignore", category=DeprecationWarning, append=1)


from win32com import universal
from win32com.server.exception import COMException
from win32com.client import gencache, DispatchWithEvents, Dispatch
import win32api
import pythoncom
from win32com.client import constants, getevents
import win32ui

import win32gui, win32con, win32clipboard # for button images!

import timer, thread

toolbar_name = "SpamBayes"

# If we are not running in a console, redirect all print statements to the
# win32traceutil collector.
# You can view output either from Pythonwin's "Tools->Trace Collector Debugging Tool",
# or simply run "win32traceutil.py" from a command prompt.
try:
    win32api.GetConsoleTitle()
except win32api.error:
    # No console - if we are running from Python sources,
    # redirect to win32traceutil, but if running from a binary
    # install, redirect to a log file.
    # Want to move to logging module later, so for now, we
    # hack together a simple logging strategy.
    if hasattr(sys, "frozen"):
        dir = win32api.GetTempPath()
        for i in range(3,0,-1):
            try: os.unlink(os.path.join(dir, "spambayes%d.log" % (i+1)))
            except os.error: pass
            try:
                os.rename(
                    os.path.join(dir, "spambayes%d.log" % i),
                    os.path.join(dir, "spambayes%d.log" % (i+1))
                    )
            except os.error: pass
        # Open this log, as unbuffered so crashes still get written.
        sys.stdout = open(os.path.join(dir,"spambayes1.log"), "wt", 0)
        sys.stderr = sys.stdout
    else:
        import win32traceutil

# We used to catch COM errors - but as most users are now on the binary, this
# niceness doesn't help anyone.

# win32com generally checks the gencache is up to date (typelib hasn't
# changed, makepy hasn't changed, etc), but when frozen we dont want to
# do this - not just for perf, but because they don't always exist!
bValidateGencache = not hasattr(sys, "frozen")
# Generate support so we get complete support including events
gencache.EnsureModule('{00062FFF-0000-0000-C000-000000000046}', 0, 9, 0,
                        bForDemand=True, bValidateFile=bValidateGencache) # Outlook 9
gencache.EnsureModule('{2DF8D04C-5BFA-101B-BDE5-00AA0044DE52}', 0, 2, 1,
                        bForDemand=True, bValidateFile=bValidateGencache) # Office 9

# Register what vtable based interfaces we need to implement.
# Damn - we should use EnsureModule for the _IDTExtensibility2 typelib, but
# win32all 155 and earlier don't like us pre-generating :(
universal.RegisterInterfaces('{AC0714F2-3D04-11D1-AE7D-00A0C90F26F4}', 0, 1, 0, ["_IDTExtensibility2"])

# A couple of functions that are in new win32all, but we dont want to
# force people to ugrade if we can avoid it.
# NOTE: Most docstrings and comments removed - see the win32all version
def CastToClone(ob, target):
    """'Cast' a COM object to another type"""
    if hasattr(target, "index"): # string like
    # for now, we assume makepy for this to work.
        if not ob.__class__.__dict__.has_key("CLSID"):
            ob = gencache.EnsureDispatch(ob)
        if not ob.__class__.__dict__.has_key("CLSID"):
            raise ValueError, "Must be a makepy-able object for this to work"
        clsid = ob.CLSID
        mod = gencache.GetModuleForCLSID(clsid)
        mod = gencache.GetModuleForTypelib(mod.CLSID, mod.LCID,
                                           mod.MajorVersion, mod.MinorVersion)
        # XXX - should not be looking in VTables..., but no general map currently exists
        # (Fixed in win32all!)
        target_clsid = mod.VTablesNamesToIIDMap.get(target)
        if target_clsid is None:
            raise ValueError, "The interface name '%s' does not appear in the " \
                              "same library as object '%r'" % (target, ob)
        mod = gencache.GetModuleForCLSID(target_clsid)
        target_class = getattr(mod, target)
        target_class = getattr(target_class, "default_interface", target_class)
        return target_class(ob) # auto QI magic happens
    raise ValueError, "Don't know what to do with '%r'" % (ob,)
try:
    from win32com.client import CastTo
except ImportError: # appears in 151 and later.
    CastTo = CastToClone

# Something else in later win32alls - like "DispatchWithEvents", but the
# returned object is not both the Dispatch *and* the event handler
def WithEventsClone(clsid, user_event_class):
    clsid = getattr(clsid, "_oleobj_", clsid)
    disp = Dispatch(clsid)
    if not disp.__dict__.get("CLSID"): # Eeek - no makepy support - try and build it.
        try:
            ti = disp._oleobj_.GetTypeInfo()
            disp_clsid = ti.GetTypeAttr()[0]
            tlb, index = ti.GetContainingTypeLib()
            tla = tlb.GetLibAttr()
            gencache.EnsureModule(tla[0], tla[1], tla[3], tla[4])
            disp_class = gencache.GetClassForProgID(str(disp_clsid))
        except pythoncom.com_error:
            raise TypeError, "This COM object can not automate the makepy process - please run makepy manually for this object"
    else:
        disp_class = disp.__class__
    clsid = disp_class.CLSID
    import new
    events_class = getevents(clsid)
    if events_class is None:
        raise ValueError, "This COM object does not support events."
    result_class = new.classobj("COMEventClass", (events_class, user_event_class), {})
    instance = result_class(disp) # This only calls the first base class __init__.
    if hasattr(user_event_class, "__init__"):
        user_event_class.__init__(instance)
    return instance

try:
    from win32com.client import WithEvents
except ImportError: # appears in 151 and later.
    WithEvents = WithEventsClone

# Whew - we seem to have all the COM support we need - let's rock!

# Determine if we have ever seen a message before.  If we have saved the spam
# field, then we know we have - but saving the spam field is an option (and may
# fail, depending on the message store).  So if no spam field, we check if
# ever been trained on.
def HaveSeenMessage(msgstore_message, manager):
    if msgstore_message.GetField(manager.config.general.field_score_name) is not None:
        return True
    # If the message has been trained on, we certainly have seen it before.
    import train
    if train.been_trained_as_ham(msgstore_message, manager) or \
       train.been_trained_as_spam(msgstore_message, manager):
        return True
    # I considered checking if the "save spam score" option is enabled - but
    # even when enabled, this sometimes fails (IMAP, hotmail)
    # Best we can do now is to assume if it is read, we have seen it.
    return msgstore_message.GetReadState()

# Function to filter a message - note it is a msgstore msg, not an
# outlook one
def ProcessMessage(msgstore_message, manager):
    manager.LogDebug(2, "ProcessMessage starting for message '%s'" \
                        % msgstore_message.subject)
    if not msgstore_message.IsFilterCandidate():
        manager.LogDebug(1, "Skipping message '%s' - we don't filter ones like that!" \
                         % msgstore_message.subject)
        return

    if HaveSeenMessage(msgstore_message, manager):
        # Already seen this message - user probably moving it back
        # after incorrect classification.
        # If enabled, re-train as Ham
        # otherwise just ignore.
        if manager.config.training.train_recovered_spam:
            subject = msgstore_message.GetSubject()
            import train
            print "Training on message '%s' - " % subject,
            if train.train_message(msgstore_message, False, manager, rescore = True):
                print "trained as good"
            else:
                print "already was trained as good"
            assert train.been_trained_as_ham(msgstore_message, manager)
            manager.SaveBayesPostIncrementalTrain()
        return
    if manager.config.filter.enabled:
        import filter
        disposition = filter.filter_message(msgstore_message, manager)
        print "Message '%s' had a Spam classification of '%s'" \
              % (msgstore_message.GetSubject(), disposition)
    else:
        print "Spam filtering is disabled - ignoring new message"
    manager.LogDebug(2, "ProcessMessage finished for", msgstore_message)

# Button/Menu and other UI event handler classes
class ButtonEvent:
    def Init(self, handler, *args):
        self.handler = handler
        self.args = args
    def Close(self):
        self.handler = self.args = None
    def OnClick(self, button, cancel):
        # Callback from Outlook - locale may have changed.
        locale.setlocale(locale.LC_NUMERIC, "C") # see locale comments above
        self.handler(*self.args)

# Folder event handler classes
class _BaseItemsEvent:
    def Init(self, target, application, manager):
        self.owner_thread_ident = thread.get_ident() # check we arent multi-threaded
        self.application = application
        self.manager = manager
        self.target = target
        self.use_timer = False
    def ReInit(self):
        pass
    def Close(self):
        self.application = self.manager = self.target = None
        self.close() # the events

class HamFolderItemsEvent(_BaseItemsEvent):
    def Init(self, *args):
        _BaseItemsEvent.Init(self, *args)
        
        start_delay = self.manager.config.experimental.timer_start_delay
        interval = self.manager.config.experimental.timer_interval
        use_timer = start_delay and interval
        if use_timer and not hasattr(timer, "__version__"):
            # No binaries will see this.
            print "*" * 50
            print "SORRY: You have tried to enable the timer, but you have a"
            print "leaky version of the 'timer' module.  These leaks prevent"
            print "Outlook from shutting down.  Please update win32all to post 154"
            print "The timer is NOT enabled..."
            print "*" * 50
            use_timer = False

        if use_timer:
            # The user wants to use a timer - see if we should only enable
            # the timer for known 'inbox' folders, or for all watched folders.
            is_inbox = self.target.IsReceiveFolder()
            if not is_inbox and self.manager.config.experimental.timer_only_receive_folders:
                use_timer = False

        # Good chance someone will assume timer is seconds, not ms.
        if use_timer and (start_delay < 500 or interval < 500):
            print "*" * 50
            print "The timer is configured to fire way too often " \
                  "(delay=%s milliseconds, interval=%s milliseconds)" \
                  % (start_delay, interval)
            print "This is very high, and is likely to starve Outlook and the "
            print "SpamBayes addin.  Please adjust your configuration"
            print "The timer is NOT enabled..."
            print "*" * 50
            use_timer = False

        self.use_timer = use_timer
        self.timer_id = None

    def ReInit(self):
        # We may have swapped between timer and non-timer.
        if self.use_timer:
            self._KillTimer()
        self.Init(self, self.target, self.application, self.manager)

    def Close(self, *args):
        self._KillTimer()
        _BaseItemsEvent.Close(self, *args)
    def _DoStartTimer(self, delay):
        assert thread.get_ident() == self.owner_thread_ident
        assert self.timer_id is None, "Shouldn't start a timer when already have one"
        # And start a new timer.
        assert delay, "No delay means no timer!"
        self.timer_id = timer.set_timer(delay, self._TimerFunc)
        self.manager.LogDebug(1, "New message timer started - id=%d, delay=%d" % (self.timer_id, delay))

    def _StartTimer(self):
        # First kill any existing timer
        self._KillTimer()
        # And start a new timer.
        delay = self.manager.config.experimental.timer_start_delay
        field_name = self.manager.config.general.field_score_name
        self.timer_generator = self.target.GetNewUnscoredMessageGenerator(field_name)
        self._DoStartTimer(delay)

    def _KillTimer(self):
        assert thread.get_ident() == self.owner_thread_ident
        if self.timer_id is not None:
            timer.kill_timer(self.timer_id)
            self.manager.LogDebug(2, "The timer with id=%d was stopped" % self.timer_id)
            self.timer_id = None

    def _TimerFunc(self, event, time):
        # Kill the timer first
        assert thread.get_ident() == self.owner_thread_ident
        self.manager.LogDebug(1, "The timer with id=%s fired" % self.timer_id)
        self._KillTimer()
        assert self.timer_generator, "Can't have a timer with no generator"
        # Callback from Outlook - locale may have changed.
        locale.setlocale(locale.LC_NUMERIC, "C") # see locale comments above
        # Find a single to item process
        # If we did manage to process one, start a new timer.
        # If we didn't, we are done and can wait until some external
        # event triggers a new timer.
        try:
            # Zoom over items I have already seen.  This is so when the spam
            # score it not saved, we do not continually look at the same old
            # unread messages (assuming they have been trained) before getting
            # to the new ones.
            # If the Spam score *is* saved, the generator should only return
            # ones that HaveSeen() returns False for, so therefore isn't a hit.
            while 1:
                item = self.timer_generator.next()
                if not HaveSeenMessage(item, self.manager):
                    break
        except StopIteration:
            # No items left in our generator
            self.timer_generator = None
            self.manager.LogDebug(1, "The new message timer found no new items, so is stopping")
        else:
            # We have an item to process - do it.
            try:
                ProcessMessage(item, self.manager)
            finally:
                # And setup the timer for the next check.
                delay = self.manager.config.experimental.timer_interval
                self._DoStartTimer(delay)

    def OnItemAdd(self, item):
        # Callback from Outlook - locale may have changed.
        locale.setlocale(locale.LC_NUMERIC, "C") # see locale comments above
        self.manager.LogDebug(2, "OnItemAdd event for folder", self,
                              "with item", item.Subject.encode("mbcs", "ignore"))
        # Due to the way our "missed message" indicator works, we do
        # a quick check here for "UnRead".  If UnRead, we assume it is very
        # new and use our timer.  If not unread, we know our missed message
        # generator would miss it, so we process it synchronously.
        if not self.use_timer or not item.UnRead:
            msgstore_message = self.manager.message_store.GetMessage(item)
            if msgstore_message is not None:
                ProcessMessage(msgstore_message, self.manager)
        else:
            self._StartTimer()

# Event fired when item moved into the Spam folder.
class SpamFolderItemsEvent(_BaseItemsEvent):
    def OnItemAdd(self, item):
        # Not sure what the best heuristics are here - for
        # now, we assume that if the calculated spam prob
        # was *not* certain-spam, or it is in the ham corpa,
        # then it should be trained as such.
        self.manager.LogDebug(2, "OnItemAdd event for SPAM folder", self,
                              "with item", item.Subject.encode("mbcs", "ignore"))
        if not self.manager.config.training.train_manual_spam:
            return
        msgstore_message = self.manager.message_store.GetMessage(item)
        if not msgstore_message.IsFilterCandidate():
            self.manager.LogDebug(1, "Not training message '%s' - we don't filter ones like that!")
            return
        if HaveSeenMessage(msgstore_message, self.manager):
            # If the message has ever been previously trained as ham, then
            # we *must* train as spam (well, we must untrain, but re-training
            # makes sense.
            # If we haven't been trained, but the spam score on the message
            # if not inside our spam threshold, then we also train as spam
            # (hopefully moving closer towards the spam threshold.)

            # Assuming that rescoring is more expensive than checking if
            # previously trained, try and optimize.
            import train
            if train.been_trained_as_ham(msgstore_message, self.manager):
                need_train = True
            else:
                prop = msgstore_message.GetField(self.manager.config.general.field_score_name)
                # We may not have been able to save the score - re-score now
                if prop is None:
                    prop = self.manager.score(msgstore_message)
                need_train = self.manager.config.filter.spam_threshold > prop * 100
            if need_train:
                subject = item.Subject.encode("mbcs", "replace")
                print "Training on message '%s' - " % subject,
                if train.train_message(msgstore_message, True, self.manager, rescore = True):
                    print "trained as spam"
                else:
                    # This shouldn't really happen, but strange shit does
                    print "already was trained as spam"
                assert train.been_trained_as_spam(msgstore_message, self.manager)
                # And if the DB can save itself incrementally, do it now
                self.manager.SaveBayesPostIncrementalTrain()

# Event function fired from the "Show Clues" UI items.
def ShowClues(mgr, explorer):
    from cgi import escape

    app = explorer.Application
    msgstore_message = explorer.GetSelectedMessages(False)
    if msgstore_message is None:
        return

    item = msgstore_message.GetOutlookItem()
    score, clues = mgr.score(msgstore_message, evidence=True)
    new_msg = app.CreateItem(0)
    # NOTE: Silly Outlook always switches the message editor back to RTF
    # once the Body property has been set.  Thus, there is no reasonable
    # way to get this as text only.  Next best then is to use HTML, 'cos at
    # least we know how to exploit it!
    body = ["<h2>Spam Score: %g</h2><br>" % score]
    push = body.append
    # Format the clues.
    push("<PRE>\n")
    push("word                                spamprob         #ham  #spam\n")
    format = " %-12g %8s %6s\n"
    c = mgr.GetClassifier()
    fetchword = c.wordinfo.get
    for word, prob in clues:
        record = fetchword(word)
        if record:
            nham = record.hamcount
            nspam = record.spamcount
        else:
            nham = nspam = "-"
        word = repr(word)
        push(escape(word) + " " * (35-len(word)))
        push(format % (prob, nham, nspam))
    push("</PRE>\n")

    # Now the raw text of the message, as best we can
    push("<h2>Message Stream:</h2><br>")
    push("<PRE>\n")
    msg = msgstore_message.GetEmailPackageObject(strip_mime_headers=False)
    push(escape(msg.as_string(), True))
    push("</PRE>\n")

    # Show all the tokens in the message
    from spambayes.tokenizer import tokenize
    from spambayes.classifier import Set # whatever classifier uses
    push("<h2>Message Tokens:</h2><br>")
    # need to re-fetch, as the tokens we see may be different based on
    # header stripping.
    toks = Set(tokenize(
        msgstore_message.GetEmailPackageObject(strip_mime_headers=True)))
    # create a sorted list
    toks = list(toks)
    toks.sort()
    push("%d unique tokens<br><br>" % len(toks))
    # Use <code> instead of <pre>, as <pre> is not word-wrapped by IE
    # However, <code> does not require escaping.
    # could use pprint, but not worth it.
    for token in toks:
        push("<code>" + repr(token) + "</code><br>\n")

    # Put the body together, then the rest of the message.
    body = ''.join(body)
    new_msg.Subject = "Spam Clues: " + item.Subject
    # As above, use HTMLBody else Outlook refuses to behave.
    new_msg.HTMLBody = "<HTML><BODY>" + body + "</BODY></HTML>"
    # Attach the source message to it
    # Using the original message has the side-effect of marking the original
    # as unread.  Tried to make a copy, but the copy then refused to delete
    # itself.
    # And the "UnRead" property of the message is not reflected in the object
    # model (we need to "refresh" the message).  Oh well.
    new_msg.Attachments.Add(item, constants.olByValue,
                            DisplayName="Original Message")
    new_msg.Display()

def CheckLatestVersion(manager):
    from spambayes.Version import get_version_string, get_version_number, fetch_latest_dict
    if hasattr(sys, "frozen"):
        version_number_key = "BinaryVersion"
        version_string_key = "Full Description Binary"
    else:
        version_number_key = "Version"
        version_string_key = "Full Description"

    app_name = "Outlook"
    cur_ver_string = get_version_string(app_name, version_string_key)
    cur_ver_num = get_version_number(app_name, version_number_key)

    try:
        win32ui.DoWaitCursor(1)
        latest = fetch_latest_dict()
        win32ui.DoWaitCursor(0)
        latest_ver_string = get_version_string(app_name, version_string_key,
                                               version_dict=latest)
        latest_ver_num = get_version_number(app_name, version_number_key,
                                            version_dict=latest)
    except:
        print "Error checking the latest version"
        traceback.print_exc()
        manager.ReportError(
            "There was an error checking for the latest version\r\n"
            "For specific details on the error, please see the SpamBayes log"
            "\r\n\r\nPlease check your internet connection, or try again later"
        )
        return

    print "Current version is %s, latest is %s." % (cur_ver_num, latest_ver_num)
    if latest_ver_num > cur_ver_num:
        url = get_version_string(app_name, "Download Page", version_dict=latest)
        msg = "You are running %s\r\n\r\nThe latest available version is %s" \
              "\r\n\r\nThe download page for the latest version is\r\n%s" \
              "\r\n\r\nWould you like to visit this page now?" \
              % (cur_ver_string, latest_ver_string, url)
        rc = win32ui.MessageBox(msg, "SpamBayes", win32con.MB_YESNO)
        if rc == win32con.IDYES:
            print "Opening browser page", url
            os.startfile(url)
    else:
        msg = "The latest available version is %s\r\n\r\n" \
              "No later version is available." % latest_ver_string
        win32ui.MessageBox(msg, "SpamBayes")

# A hook for whatever tests we have setup
def Tester(manager):
    import tester
    # This is only used in source-code versions - so we may as well reload
    # the test suite to save shutting down Outlook each time we tweak it.
    reload(tester)
    try:
        print "Executing automated tests..."
        tester.test(manager)
        print "Tests worked."
    except:
        traceback.print_exc()
        print "Tests FAILED.  Sorry about that.  If I were you, I would do a full re-train ASAP"
        print "Please delete any test messages from your Spam, Unsure or Inbox folders first."

# The "Delete As Spam" and "Recover Spam" button
# The event from Outlook's explorer that our folder has changed.
class ButtonDeleteAsEventBase:
    def Init(self, manager, explorer):
        self.manager = manager
        self.explorer = explorer

    def Close(self):
        self.manager = self.explorer = None

class ButtonDeleteAsSpamEvent(ButtonDeleteAsEventBase):
    def OnClick(self, button, cancel):
        msgstore = self.manager.message_store
        msgstore_messages = self.explorer.GetSelectedMessages(True)
        if not msgstore_messages:
            return
        # If we are not yet enabled, tell the user.
        # (This is better than disabling the button as a) the user may not
        # understand why it is disabled, and b) as we would then need to check
        # the button state as the manager dialog closes.
        if not self.manager.config.filter.enabled:
            self.manager.ReportError(
                "You must enable SpamBayes before you can delete as spam")
            return
        win32ui.DoWaitCursor(1)
        # Delete this item as spam.
        spam_folder = None
        spam_folder_id = self.manager.config.filter.spam_folder_id
        if spam_folder_id:
            spam_folder = msgstore.GetFolder(spam_folder_id)
        if not spam_folder:
            self.manager.ReportError("You must configure the Spam folder",
                               "Invalid Configuration")
            return
        import train
        new_msg_state = self.manager.config.general.delete_as_spam_message_state
        for msgstore_message in msgstore_messages:
            # Must train before moving, else we lose the message!
            subject = msgstore_message.GetSubject()
            print "Moving and spam training message '%s' - " % (subject,),
            if train.train_message(msgstore_message, True, self.manager, rescore = True):
                print "trained as spam"
            else:
                print "already was trained as spam"
            # Do the new message state if necessary.
            try:
                if new_msg_state == "Read":
                    msgstore_message.SetReadState(True)
                elif new_msg_state == "Unread":
                    msgstore_message.SetReadState(False)
                else:
                    if new_msg_state not in ["", "None", None]:
                        print "*** Bad new_msg_state value: %r" % (new_msg_state,)
            except pythoncom.com_error:
                print "*** Failed to set the message state to '%s' for message '%s'" % (new_msg_state, subject)
            # Now move it.
            msgstore_message.MoveTo(spam_folder)
            # Note the move will possibly also trigger a re-train
            # but we are smart enough to know we have already done it.
        # And if the DB can save itself incrementally, do it now
        self.manager.SaveBayesPostIncrementalTrain()
        win32ui.DoWaitCursor(0)

class ButtonRecoverFromSpamEvent(ButtonDeleteAsEventBase):
    def OnClick(self, button, cancel):
        msgstore = self.manager.message_store
        msgstore_messages = self.explorer.GetSelectedMessages(True)
        if not msgstore_messages:
            return
        # If we are not yet enabled, tell the user.
        # (This is better than disabling the button as a) the user may not
        # understand why it is disabled, and b) as we would then need to check
        # the button state as the manager dialog closes.
        if not self.manager.config.filter.enabled:
            self.manager.ReportError(
                "You must enable SpamBayes before you can recover spam")
            return
        win32ui.DoWaitCursor(1)
        # Get the inbox as the default place to restore to
        # (incase we dont know (early code) or folder removed etc
        app = self.explorer.Application
        inbox_folder = msgstore.GetFolder(
                    app.Session.GetDefaultFolder(constants.olFolderInbox))
        new_msg_state = self.manager.config.general.recover_from_spam_message_state
        import train
        for msgstore_message in msgstore_messages:
            # Recover where they were moved from
            # During experimenting/playing/debugging, it is possible
            # that the source folder == dest folder - restore to
            # the inbox in this case.
            restore_folder = msgstore_message.GetRememberedFolder()
            if restore_folder is None or \
               msgstore_message.GetFolder() == restore_folder:
                restore_folder = inbox_folder

            # Must train before moving, else we lose the message!
            subject = msgstore_message.GetSubject()
            print "Recovering to folder '%s' and ham training message '%s' - " % (restore_folder.name, subject),
            if train.train_message(msgstore_message, False, self.manager, rescore = True):
                print "trained as ham"
            else:
                print "already was trained as ham"
            # Do the new message state if necessary.
            try:
                if new_msg_state == "Read":
                    msgstore_message.SetReadState(True)
                elif new_msg_state == "Unread":
                    msgstore_message.SetReadState(False)
                else:
                    if new_msg_state not in ["", "None", None]:
                        print "*** Bad new_msg_state value: %r" % (new_msg_state,)
            except pythoncom.com_error:
                print "*** Failed to set the message state to '%s' for message '%s'" % (new_msg_state, subject)
            # Now move it.
            msgstore_message.MoveTo(restore_folder)
            # Note the move will possibly also trigger a re-train
            # but we are smart enough to know we have already done it.
        # And if the DB can save itself incrementally, do it now
        self.manager.SaveBayesPostIncrementalTrain()
        win32ui.DoWaitCursor(0)

# Helpers to work with images on buttons/toolbars.
def SetButtonImage(button, fname, manager):
    # whew - http://support.microsoft.com/default.aspx?scid=KB;EN-US;q288771
    # shows how to make a transparent bmp.
    # Also note that the clipboard takes ownership of the handle -
    # this, we can not simply perform this load once and reuse the image.
    if not os.path.isabs(fname):
        # images relative to the application path
        fname = os.path.join(manager.application_directory,
                                 "images", fname)
    if not os.path.isfile(fname):
        print "WARNING - Trying to use image '%s', but it doesn't exist" % (fname,)
        return None
    handle = win32gui.LoadImage(0, fname, win32con.IMAGE_BITMAP, 0, 0, win32con.LR_DEFAULTSIZE | win32con.LR_LOADFROMFILE)
    win32clipboard.OpenClipboard()
    win32clipboard.SetClipboardData(win32con.CF_BITMAP, handle)
    win32clipboard.CloseClipboard()
    button.Style = constants.msoButtonIconAndCaption
    button.PasteFace()

# A class that manages an "Outlook Explorer" - that is, a top-level window
# All UI elements are managed here, and there is one instance per explorer.
class ExplorerWithEvents:
    def Init(self, manager, explorers_collection):
        self.manager = manager
        self.have_setup_ui = False
        self.explorers_collection = explorers_collection
        self.toolbar = None

    def SetupUI(self):
        manager = self.manager
        activeExplorer = self
        assert self.toolbar is None, "Should not yet have a toolbar"

        # Add our "Delete as ..." and "Recover from" buttons
        tt_text = "Move the selected message to the Spam folder,\n" \
                  "and train the system that this is Spam."
        self.but_delete_as = self._AddControl(
                        None,
                        constants.msoControlButton,
                        ButtonDeleteAsSpamEvent, (self.manager, self),
                        Caption="Delete As Spam",
                        TooltipText = tt_text,
                        BeginGroup = False,
                        Tag = "SpamBayesCommand.DeleteAsSpam",
                        image = "delete_as_spam.bmp")
        # And again for "Recover from"
        tt_text = \
                "Recovers the selected item back to the folder\n" \
                "it was filtered from (or to the Inbox if this\n" \
                "folder is not known), and trains the system that\n" \
                "this is a good message\n"
        self.but_recover_as = self._AddControl(
                        None,
                        constants.msoControlButton,
                        ButtonRecoverFromSpamEvent, (self.manager, self),
                        Caption="Recover from Spam",
                        TooltipText = tt_text,
                        Tag = "SpamBayesCommand.RecoverFromSpam",
                        image = "recover_ham.bmp")

        # The main tool-bar dropdown with all our entries.
        # Add a pop-up menu to the toolbar
        popup = self._AddControl(
                        None,
                        constants.msoControlPopup,
                        None, None,
                        Caption="SpamBayes",
                        TooltipText = "SpamBayes anti-spam filters and functions",
                        Enabled = True,
                        Tag = "SpamBayesCommand.Popup")
        if popup is not None: # We may not be able to find/create our button
            # Convert from "CommandBarItem" to derived
            # "CommandBarPopup" Not sure if we should be able to work
            # this out ourselves, but no introspection I tried seemed
            # to indicate we can.  VB does it via strongly-typed
            # declarations.
            popup = CastTo(popup, "CommandBarPopup")
            # And add our children.
            self._AddControl(popup,
                           constants.msoControlButton,
                           ButtonEvent, (manager.ShowManager,),
                           Caption="SpamBayes Manager...",
                           TooltipText = "Show the SpamBayes manager dialog.",
                           Enabled = True,
                           Visible=True,
                           Tag = "SpamBayesCommand.Manager")
            self._AddControl(popup,
                           constants.msoControlButton,
                           ButtonEvent, (ShowClues, self.manager, self),
                           Caption="Show spam clues for current message",
                           Enabled=True,
                           Visible=True,
                           Tag = "SpamBayesCommand.Clues")
            self._AddControl(popup,
                           constants.msoControlButton,
                           ButtonEvent, (CheckLatestVersion, self.manager,),
                           Caption="Check for new version",
                           Enabled=True,
                           Visible=True,
                           Tag = "SpamBayesCommand.CheckVersion")
        # If we are running from Python sources, enable a few extra items
        if not hasattr(sys, "frozen"):
            self._AddControl(popup,
                           constants.msoControlButton,
                           ButtonEvent, (Tester, self.manager),
                           Caption="Execute test suite",
                           Enabled=True,
                           Visible=True,
                           Tag = "SpamBayesCommand.TestSuite")
        self.have_setup_ui = True

    def _AddControl(self,
                    parent, # who the control is added to
                    control_type, # type of control to add.
                    events_class, events_init_args, # class/Init() args
                    **item_attrs): # extra control attributes.
        # Outlook Toolbars suck :)
        # We have tried a number of options: temp/perm in the standard toolbar,
        # Always creating our own toolbar, etc.
        # This seems to be fairly common:
        # http://groups.google.com/groups?threadm=eKKmbvQvAHA.1808%40tkmsftngp02
        # Now the strategy is just to use our own, permanent toolbar, with
        # permanent items, and ignore uninstall issues.
        # We search all commandbars for a control with our Tag.  If found, we
        # use it (the user may have customized the bar and moved our buttons
        # elsewhere).  If we can not find the child control, we then try and
        # locate our toolbar, creating if necessary.  Our items get added to
        # that.
        assert item_attrs.has_key('Tag'), "Need a 'Tag' attribute!"
        image_fname = None
        if 'image' in item_attrs:
            image_fname = item_attrs['image']
            del item_attrs['image']

        tag = item_attrs["Tag"]
        item = self.CommandBars.FindControl(
                        Type = control_type,
                        Tag = tag)
        if item is None:
            if parent is None:
                # No parent specified - that means top-level - locate the
                # toolbar to use as the parent.
                if self.toolbar is None:
                    # See if we can find our "SpamBayes" toolbar
                    # Indexing via the name appears unreliable, so just loop
                    # Pity we have no "Tag" on a toolbar - then we could even
                    # handle being renamed by the user.
                    bars = self.CommandBars
                    for i in range(bars.Count):
                        toolbar = bars.Item(i+1)
                        if toolbar.Name == "SpamBayes":
                            self.toolbar = toolbar
                            print "Found SB toolbar - visible state is", toolbar.Visible
                            break
                    else:
                        # for not broken - can't find toolbar.  Create a new one.
                        # Create it as a permanent one (which is default)
                        if self.explorers_collection.have_created_toolbar:
                            # Eeek - we have already created a toolbar, but
                            # now we can't find it.  It is likely this is the
                            # first time we are being run, and outlook is
                            # being started with multiple Windows open.
                            # Hopefully things will get back to normal once
                            # Outlook is restarted (which testing shows it does)
                            return

                        print "Creating new SpamBayes toolbar to host our buttons"
                        self.toolbar = bars.Add(toolbar_name, constants.msoBarTop, Temporary=False)
                        self.explorers_collection.have_created_toolbar = True
                    self.toolbar.Visible = True
                parent = self.toolbar
            # Now add the item itself to the parent.
            try:
                item = parent.Controls.Add(Type=control_type, Temporary=False)
            except pythoncom.com_error, e:
                # Toolbars seem to still fail randomly for some users.
                # eg, bug [ 755738 ] Latest CVS outllok doesn't work
                print "FAILED to add the toolbar item '%s' - %s" % (tag,e)
                return
            if image_fname:
                # Eeek - only available in derived class.
                assert control_type == constants.msoControlButton
                but = CastTo(item, "_CommandBarButton")
                SetButtonImage(but, image_fname, self.manager)
            # Set the extra attributes passed in.
            for attr, val in item_attrs.items():
                setattr(item, attr, val)

        # Hook events for the item, but only if we haven't already in some
        # other explorer instance.
        if events_class is not None and tag not in self.explorers_collection.button_event_map:
            item = DispatchWithEvents(item, events_class)
            item.Init(*events_init_args)
            # We must remember the item itself, else the events get disconnected
            # as the item destructs.
            self.explorers_collection.button_event_map[tag] = item
        return item

    def GetSelectedMessages(self, allow_multi = True, explorer = None):
        if explorer is None:
            explorer = self.Application.ActiveExplorer()
        sel = explorer.Selection
        if sel.Count > 1 and not allow_multi:
            self.manager.ReportError("Please select a single item", "Large selection")
            return None

        ret = []
        for i in range(sel.Count):
            item = sel.Item(i+1)
            msgstore_message = self.manager.message_store.GetMessage(item)
            if msgstore_message and msgstore_message.IsFilterCandidate():
                ret.append(msgstore_message)

        if len(ret) == 0:
            self.manager.ReportError("No filterable mail items are selected", "No selection")
            return None
        if allow_multi:
            return ret
        return ret[0]

    # The Outlook event handlers
    def OnActivate(self):
        self.manager.LogDebug(3, "OnActivate", self)
        # See comments for OnNewExplorer below.
        # *sigh* - OnActivate seems too early too for Outlook 2000,
        # but Outlook 2003 seems to work here, and *not* the folder switch etc
        # Outlook 2000 crashes when a second window is created and we use this
        # event
        # OnViewSwitch however seems useful, so we ignore this.
        pass

    def OnSelectionChange(self):
        self.manager.LogDebug(3, "OnSelectionChange", self)
        # See comments for OnNewExplorer below.
        if not self.have_setup_ui:
            self.SetupUI()
            # Prime the button views.
            self.OnFolderSwitch()

    def OnClose(self):
        self.manager.LogDebug(3, "OnClose", self)
        self.explorers_collection._DoDeadExplorer(self)
        self.explorers_collection = None
        self.toolbar = None
        self.close() # disconnect events.

    def OnBeforeFolderSwitch(self, new_folder, cancel):
        self.manager.LogDebug(3, "OnBeforeFolderSwitch", self)

    def OnFolderSwitch(self):
        self.manager.LogDebug(3, "OnFolderSwitch", self)
        # Yet another worm-around for our event timing woes.  This may
        # be the first event ever seen for this explorer if, eg,
        # "Outlook Today" is the initial Outlook view.
        if not self.have_setup_ui:
            self.SetupUI()
        # Work out what folder we are in.
        outlook_folder = self.CurrentFolder
        if outlook_folder is None or \
           outlook_folder.DefaultItemType != constants.olMailItem:
            show_delete_as = False
            show_recover_as = False
        else:
            show_delete_as = True
            show_recover_as = False
            try:
                mapi_folder = self.manager.message_store.GetFolder(outlook_folder)
                look_id = self.manager.config.filter.spam_folder_id
                if mapi_folder is not None and look_id:
                    look_folder = self.manager.message_store.GetFolder(look_id)
                    if mapi_folder == look_folder:
                        # This is the Spam folder - only show "recover"
                        show_recover_as = True
                        show_delete_as = False
                # Check if uncertain
                look_id = self.manager.config.filter.unsure_folder_id
                if mapi_folder is not None and look_id:
                    look_folder = self.manager.message_store.GetFolder(look_id)
                    if mapi_folder == look_folder:
                        show_recover_as = True
                        show_delete_as = True
            except:
                print "Error finding the MAPI folders for a folder switch event"
                traceback.print_exc()
        if self.but_recover_as is not None:
            self.but_recover_as.Visible = show_recover_as
        if self.but_delete_as is not None:
            self.but_delete_as.Visible = show_delete_as

    def OnBeforeViewSwitch(self, new_view, cancel):
        self.manager.LogDebug(3, "OnBeforeViewSwitch", self)

    def OnViewSwitch(self):
        self.manager.LogDebug(3, "OnViewSwitch", self)
        if not self.have_setup_ui:
            self.SetupUI()

# Events from our "Explorers" collection (not an Explorer instance)
class ExplorersEvent:
    def Init(self, manager):
        assert manager
        self.manager = manager
        self.explorers = []
        self.have_created_toolbar = False
        self.button_event_map = {}

    def Close(self):
        self.explorers = None

    def _DoNewExplorer(self, explorer):
        explorer = DispatchWithEvents(explorer, ExplorerWithEvents)
        explorer.Init(self.manager, self)
        self.explorers.append(explorer)
        return explorer

    def _DoDeadExplorer(self, explorer):
        self.explorers.remove(explorer)
        if len(self.explorers)==0:
            # No more explorers - disconnect all events.
            # (not doing this causes shutdown problems)
            for tag, button in self.button_event_map.items():
                closer = getattr(button, "Close", None)
                if closer is not None:
                    closer()
            self.button_event_map = {}

    def OnNewExplorer(self, explorer):
        # NOTE - Outlook has a bug, as confirmed by many on Usenet, in
        # that OnNewExplorer is too early to access the CommandBars
        # etc elements. We hack around this by putting the logic in
        # the first OnActivate call of the explorer itself.
        # Except that doesn't always work either - sometimes
        # OnActivate will cause a crash when selecting "Open in New Window",
        # so we tried OnSelectionChanges, which works OK until there is a
        # view with no items (eg, Outlook Today) - so at the end of the
        # day, we can never assume we have been initialized!
        self._DoNewExplorer(explorer)

# The outlook Plugin COM object itself.
class OutlookAddin:
    _com_interfaces_ = ['_IDTExtensibility2']
    _public_methods_ = []
    _reg_clsctx_ = pythoncom.CLSCTX_INPROC_SERVER
    _reg_clsid_ = "{3556EDEE-FC91-4cf2-A0E4-7489747BAB10}"
    _reg_progid_ = "SpamBayes.OutlookAddin"
    _reg_policy_spec_ = "win32com.server.policy.EventHandlerPolicy"

    def __init__(self):
        self.folder_hooks = {}
        self.application = None

    def OnConnection(self, application, connectMode, addin, custom):
        # Handle failures during initialization so that we are not
        # automatically disabled by Outlook.
        # Our error reporter is in the "manager" module, so we get that first
        locale.setlocale(locale.LC_NUMERIC, "C") # see locale comments above
        import manager
        try:
            self.application = application

            self.manager = None # if we die while creating it!
            # Create our bayes manager
            self.manager = manager.GetManager(application)
            assert self.manager.addin is None, "Should not already have an addin"
            self.manager.addin = self
    
            # Only now will the import of "spambayes.Version" work, as the
            # manager is what munges sys.path for us.
            from spambayes.Version import get_version_string
            version_key = "Full Description"
            if hasattr(sys, "frozen"): version_key += " Binary"
            print "%s starting (with engine %s)" % \
                    (get_version_string("Outlook", version_key),
                     get_version_string())
            major, minor, spack, platform, ver_str = win32api.GetVersionEx()
            print "on Windows %d.%d.%d (%s)" % \
                  (major, minor, spack, ver_str)
            print "using Python", sys.version

            self.explorers_events = None # create at OnStartupComplete

            if self.manager.config.filter.enabled:
                # A little "sanity test" to help the user.  If our status is
                # 'enabled', then it means we have previously managed to
                # convince the manager dialog we have enough ham/spam and
                # valid folders.  If for some reason, we have zero ham or spam,
                # or no folder definitions but are 'enabled', then it is likely
                # something got hosed and the user doesn't know.
                if self.manager.bayes.nham==0 or \
                   self.manager.bayes.nspam==0 or \
                   not self.manager.config.filter.spam_folder_id or \
                   not self.manager.config.filter.watch_folder_ids:
                    msg = "It appears there was an error loading your configuration\r\n\r\n" \
                          "Please re-configure SpamBayes via the SpamBayes dropdown"
                    self.manager.ReportError(msg)
                # But continue on regardless.
                self.FiltersChanged()
                try:
                    self.ProcessMissedMessages()
                except:
                    print "Error processing missed messages!"
                    traceback.print_exc()
        except:
            print "Error connecting to Outlook!"
            traceback.print_exc()
            manager.ReportError(
                "There was an error initializing the SpamBayes addin\r\n\r\n"
                "Please re-start Outlook and try again.")

    def ProcessMissedMessages(self):
        # This could possibly spawn threads if it was too slow!
        from time import clock
        config = self.manager.config.filter
        manager = self.manager
        field_name = manager.config.general.field_score_name
        for folder in manager.message_store.GetFolderGenerator(
                                    config.watch_folder_ids,
                                    config.watch_include_sub):
            event_hook = self._GetHookForFolder(folder)
            if event_hook.use_timer:
                print "Processing missed spam in folder '%s' by starting a timer" \
                      % (folder.name,)
                event_hook._StartTimer()
            else:
                num = 0
                start = clock()
                for message in folder.GetNewUnscoredMessageGenerator(field_name):
                    ProcessMessage(message, manager)
                    num += 1
                # See if perf hurts anyone too much.
                print "Processing %d missed spam in folder '%s' took %gms" \
                      % (num, folder.name, (clock()-start)*1000)

    def FiltersChanged(self):
        try:
            # Create a notification hook for all folders we filter.
            self.UpdateFolderHooks()
        except:
            self.manager.ReportFatalStartupError(
                "Could not watch the specified folders")

    def UpdateFolderHooks(self):
        config = self.manager.config.filter
        new_hooks = {}
        new_hooks.update(
            self._HookFolderEvents(config.watch_folder_ids,
                                   config.watch_include_sub,
                                   HamFolderItemsEvent)
            )
        # For spam manually moved
        if config.spam_folder_id:
            new_hooks.update(
                self._HookFolderEvents([config.spam_folder_id],
                                       False,
                                       SpamFolderItemsEvent)
                )
        for k in self.folder_hooks.keys():
            if not new_hooks.has_key(k):
                self.folder_hooks[k].Close()
        self.folder_hooks = new_hooks

    def _GetHookForFolder(self, folder):
        ret = self.folder_hooks[folder.id]
        assert ret.target == folder
        return ret

    def _HookFolderEvents(self, folder_ids, include_sub, HandlerClass):
        new_hooks = {}
        for msgstore_folder in self.manager.message_store.GetFolderGenerator(
                    folder_ids, include_sub):
            existing = self.folder_hooks.get(msgstore_folder.id)
            if existing is None or existing.__class__ != HandlerClass:
                folder = msgstore_folder.GetOutlookItem()
                name = msgstore_folder.name
                try:
                    new_hook = DispatchWithEvents(folder.Items, HandlerClass)
                except ValueError:
                    print "WARNING: Folder '%s' can not hook events" % (name,)
                    new_hook = None
                if new_hook is not None:
                    new_hook.Init(msgstore_folder, self.application, self.manager)
                    new_hooks[msgstore_folder.id] = new_hook
                    try:
                        self.manager.EnsureOutlookFieldsForFolder(msgstore_folder.GetID())
                    except:
                        # An exception checking that Outlook's folder has a
                        # 'spam' field is not fatal, nor really even worth
                        # telling the user about, nor even worth a traceback
                        # (as it is likely a COM error).
                        print "ERROR: Failed to check folder '%s' for " \
                              "Spam field" % name
                        etype, value, tb = sys.exc_info()
                        tb = None # dont want it, and nuke circular ref
                        traceback.print_exception(etype, value, tb)
                    print "SpamBayes: Watching for new messages in folder ", name
            else:
                new_hooks[msgstore_folder.id] = existing
                exiting.ReInit()
        return new_hooks

    def OnDisconnection(self, mode, custom):
        print "SpamBayes - Disconnecting from Outlook"
        if self.folder_hooks:
            for hook in self.folder_hooks.values():
                hook.Close()
            self.folder_hooks = None
        self.application = None
        self.explorers_events = None
        if self.manager is not None:
            # Save database - bsddb databases will generally do nothing here
            # as it will not be dirty, but pickles will.
            # config never needs saving as it is always done by whoever changes
            # it (ie, the dialog)
            self.manager.Save()
            stats = self.manager.stats
            print "SpamBayes processed %d messages, finding %d spam and %d unsure" % \
                (stats.num_seen, stats.num_spam, stats.num_unsure)
            self.manager.Close()
            self.manager = None

        print "Addin terminating: %d COM client and %d COM servers exist." \
              % (pythoncom._GetInterfaceCount(), pythoncom._GetGatewayCount())
        try:
            # will be available if "python_d addin.py" is used to
            # register the addin.
            total_refs = sys.gettotalrefcount() # debug Python builds only
            print "%d Python references exist" % (total_refs,)
        except AttributeError:
            pass

    def OnAddInsUpdate(self, custom):
        pass
    def OnStartupComplete(self, custom):
        # Toolbar and other UI stuff must be setup once startup is complete.
        explorers = self.application.Explorers
        if self.manager is not None: # If we successfully started up.
            # and Explorers events so we know when new explorers spring into life.
            self.explorers_events = WithEvents(explorers, ExplorersEvent)
            self.explorers_events.Init(self.manager)
            # And hook our UI elements to all existing explorers
            for i in range(explorers.Count):
                explorer = explorers.Item(i+1)
                explorer = self.explorers_events._DoNewExplorer(explorer)
                explorer.OnFolderSwitch()

    def OnBeginShutdown(self, custom):
        pass

def _DoRegister(klass, root):
    import _winreg
    key = _winreg.CreateKey(root,
                            "Software\\Microsoft\\Office\\Outlook\\Addins")
    subkey = _winreg.CreateKey(key, klass._reg_progid_)
    _winreg.SetValueEx(subkey, "CommandLineSafe", 0, _winreg.REG_DWORD, 0)
    _winreg.SetValueEx(subkey, "LoadBehavior", 0, _winreg.REG_DWORD, 3)
    _winreg.SetValueEx(subkey, "Description", 0, _winreg.REG_SZ, "SpamBayes anti-spam tool")
    _winreg.SetValueEx(subkey, "FriendlyName", 0, _winreg.REG_SZ, "SpamBayes")

def RegisterAddin(klass):
    import _winreg
    # Try and register twice - once in HKLM, and once in HKCU.  This seems
    # to help roaming profiles, etc.  Once registered, it is both registered
    # on this machine for the current user (even when they roam, assuming it
    # has been installed on the remote machine also) and for any user on this
    # machine.
    try:
        _DoRegister(klass, _winreg.HKEY_LOCAL_MACHINE)
    except WindowsError:
        # But they may not have the rights to install there.
        pass
    # We don't catch exception registering just for this user though
    # that is fatal!
    _DoRegister(klass, _winreg.HKEY_CURRENT_USER)
    print "Registration complete."

def UnregisterAddin(klass):
    import _winreg
    try:
        _winreg.DeleteKey(_winreg.HKEY_LOCAL_MACHINE,
                          "Software\\Microsoft\\Office\\Outlook\\Addins\\" \
                          + klass._reg_progid_)
    except WindowsError:
        pass
    # and again for current user.
    try:
        _winreg.DeleteKey(_winreg.HKEY_CURRENT_USER,
                          "Software\\Microsoft\\Office\\Outlook\\Addins\\" \
                          + klass._reg_progid_)
    except WindowsError:
        pass

if __name__ == '__main__':
    import win32com.server.register
    win32com.server.register.UseCommandLine(OutlookAddin)
    if "--unregister" in sys.argv:
        UnregisterAddin(OutlookAddin)
    else:
        RegisterAddin(OutlookAddin)
