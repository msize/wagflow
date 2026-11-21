"""Submission guards exercised with captured frames; no terminal or network."""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wag
import peerchat
import relay
from frames import (CLAUDE_IDLE, CLAUDE_RUNNING, CODEX_IDLE, CODEX_QUEUED, CODEX_UNSUBMITTED,
                    CODEX_0158_IDLE, CODEX_0158_TEXT, CODEX_0158_TYPED, CODEX_MULTILINE_STATUS,
                    CODEX_MULTILINE_STATUS_TYPED, CODEX_THREAD_STATUS,
                    CODEX_WARNINGS_FOOTER_IDLE, CODEX_WARNINGS_FOOTER_TEXT, CODEX_WARNINGS_FOOTER_TYPED,
                    CLAUDE_SUGGESTION, CLAUDE_WRAPPED_DRAFT, TEXT, Clock, FakeWag, claude, codex, stable_frames)



class Submission(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    @contextlib.contextmanager
    def environment(self, fake):
        with patch.object(wag, 'pane_text', fake.pane_text), patch.object(wag, 'type_into', fake.type_into), \
                patch.object(wag, 'cursor_column', fake.cursor_column), \
                patch.object(peerchat, 'codex_cursor_in_composer', return_value=True), \
                patch.object(wag, 'request', side_effect=AssertionError('real terminal request')), \
                patch.object(peerchat, 'now', self.clock.now), patch.object(peerchat, 'pause', self.clock.pause):
            yield

    def send(self, fake, text=TEXT):
        with self.environment(fake):
            try:
                return peerchat.send('pane', peerchat.PROFILES[fake.tool], text, dry_run=False, retry=True)
            finally:
                self.assertEqual(1, fake.keys.count(text), 'a send must never retype its text')

    def test_submitted_first_time_for_both_profiles(self):
        for tool, key in [('codex', '\t'), ('claude', '\n')]:
            with self.subTest(tool=tool):
                fake = FakeWag(tool)
                self.assertEqual('submitted', self.send(fake))
                self.assertEqual([TEXT, key], fake.keys)

    def test_captured_queue_identifies_this_message_only(self):
        for frame, expected in [(CODEX_QUEUED, 'queued'),
                                (CODEX_QUEUED.replace('193916Z', '193917Z'), 'submitted')]:
            with self.subTest(expected=expected):
                fake = FakeWag(after=lambda f: frame)
                self.assertEqual(expected, self.send(fake))
                self.assertEqual([TEXT, '\t'], fake.keys)

    def test_queue_label_requires_matching_text_as_well_as_id(self):
        frame = CODEX_QUEUED.replace('step 1:', 'step 2:')
        self.assertEqual('submitted', self.send(FakeWag(after=lambda f: frame)))

    def test_claude_queued_placeholder_after_our_submit_reports_queued(self):
        frame = claude('Press up to edit queued messages')
        fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE, claude(TEXT), frame))
        self.assertEqual('queued', self.send(fake))
        self.assertEqual([TEXT, '\n'], fake.keys)

    def test_claude_queue_placeholder_before_typing_is_refused_as_a_possible_draft(self):
        frame = claude('Press up to edit queued messages')
        fake = FakeWag('claude', frames=[frame])
        with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'holds a draft'):
            peerchat.send_once('pane', peerchat.PROFILES['claude'], TEXT, dry_run=False)
        self.assertEqual([], fake.keys)

    def test_claude_queue_placeholder_with_extra_content_is_not_empty(self):
        for extra in [' and an unsent draft', '\n  1. Yes\n  2. No']:
            frame = claude('Press up to edit queued messages' + extra)
            self.assertFalse(peerchat.looks_empty(peerchat.PROFILES['claude'],
                                                  peerchat.claude_composer(frame)))
            fake = FakeWag('claude', after=lambda f: frame)
            with self.assertRaises(peerchat.Failed):
                self.send(fake)
            self.assertEqual([TEXT, '\n'], fake.keys)

    def test_clipped_queue_prefix_needs_an_id_and_later_entries_are_checked(self):
        clipped = TEXT.split('[id ', 1)[0].rstrip()
        self.assertFalse(peerchat.owns(clipped, TEXT))
        queue = '• Queued follow-up inputs\n  ↳ ' + clipped + '\n'
        for entries, outcome in [(queue, 'submitted'), (queue + '  ↳ ' + TEXT + '\n', 'queued')]:
            with self.subTest(outcome=outcome):
                frame = codex('Ask Codex to do anything', entries)
                self.assertEqual(outcome, self.send(FakeWag(after=lambda f: frame)))

    def test_submit_and_queue_after_retries_report_the_path(self):
        for retry, completed, expected in [(1, CODEX_IDLE, 'submitted after retry 1'),
                                            (2, CODEX_QUEUED, 'queued after retry 2')]:
            with self.subTest(retry=retry):
                fake = FakeWag(after=lambda f: completed if len(f.keys) >= retry + 2 else codex(TEXT))
                self.assertEqual(expected, self.send(fake))
                self.assertEqual([TEXT] + ['\t'] * (retry + 1), fake.keys)

    def test_idle_codex_uses_one_return_after_tabs_fail(self):
        fake = FakeWag(after=lambda f: CODEX_IDLE if f.keys[-1] == '\n' else codex(TEXT))
        self.assertEqual('submitted after Return', self.send(fake))
        self.assertEqual([TEXT, '\t', '\t', '\t', '\n'], fake.keys)

    def test_exhausted_retries_quote_the_composer_without_retyping(self):
        for tool, keys in [('codex', ['\t', '\t', '\t', '\n']), ('claude', ['\n'] * 3)]:
            with self.subTest(tool=tool):
                fake = FakeWag(tool, after=lambda f: codex(TEXT) if f.tool == 'codex' else claude(TEXT))
                with self.assertRaisesRegex(peerchat.Failed, 'pointer still unsent') as caught:
                    self.send(fake)
                self.assertIn(repr(TEXT), str(caught.exception))
                self.assertEqual([TEXT] + keys, fake.keys)

    def test_busy_or_queued_evidence_anywhere_vetoes_return(self):
        for marker, padding in [('Working (esc to interrupt)', 0), ('Working (esc to interrupt)', 20),
                                ('Working (esc to interrupt)', 50), ('Queued follow-up inputs', 50)]:
            with self.subTest(marker=marker, padding=padding):
                frame = codex(TEXT, marker + '\n' + 'old row\n' * padding)
                fake = FakeWag(after=lambda f: frame)
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                self.assertEqual([TEXT, '\t', '\t', '\t'], fake.keys)

    def test_old_same_id_queue_cannot_hide_unsent_or_changed_composer(self):
        for draft in [TEXT, 'check if codex replied']:
            with self.subTest(draft=draft):
                frame = CODEX_QUEUED.replace('› Ask Codex to do anything', '› ' + draft)
                fake = FakeWag(after=lambda f: frame)
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                expected = ['\t'] * (3 if draft == TEXT else 1)
                self.assertEqual([TEXT] + expected, fake.keys)

    def test_changed_drafts_fail_before_first_submit_and_during_polling(self):
        for draft in ['check if codex replied', TEXT + ' and more', TEXT.replace('step 1:', 'step 2:')]:
            for before_submit in [True, False]:
                with self.subTest(draft=draft, before_submit=before_submit):
                    fake = (FakeWag(frames=stable_frames(CODEX_IDLE, codex(draft))) if before_submit
                            else FakeWag(after=lambda f: codex(draft)))
                    with self.assertRaisesRegex(peerchat.Failed, 'other than the attempted pointer'):
                        self.send(fake)
                    self.assertEqual([TEXT] + ([] if before_submit else ['\t']), fake.keys)

    def test_short_wrapped_and_clipped_text_can_be_verified(self):
        marker_start, marker_end = TEXT.index('[id '), TEXT.index(']') + 1
        cases = [('hello', 'hello'), (TEXT, TEXT[:70] + '\n  ' + TEXT[70:]),
                 (TEXT, TEXT[:marker_end]), (TEXT, TEXT[marker_start:]),
                 (TEXT, TEXT[marker_start:marker_end + 10])]
        for text, rendered in cases:
            with self.subTest(rendered=rendered):
                # A clipped match must hold for VERIFY_STABLE: three polls of two reads each.
                reads = 2 if peerchat.compact(rendered) == peerchat.compact(text) else 6
                fake = FakeWag(frames=[CODEX_IDLE] * 2 + [codex(rendered)] * reads + [CODEX_IDLE] * 2)
                self.assertEqual('submitted', self.send(fake, text))
                self.assertEqual([text, '\t'], fake.keys)

    def test_clipped_match_must_hold_before_the_submit_key(self):
        clipped = TEXT[:TEXT.index(']') + 1]
        self.assertTrue(peerchat.owns(clipped, TEXT))
        fake = FakeWag(frames=stable_frames(CODEX_IDLE, codex(clipped), codex(TEXT + ' extra')))
        with self.assertRaisesRegex(peerchat.Failed, 'other than the attempted pointer'):
            self.send(fake)
        self.assertEqual([TEXT], fake.keys)

    def test_late_drawn_pointer_is_still_verified_and_submitted(self):
        # Codex 0.170 drew a 224-char pointer more than 3 s after it was typed.
        late = 2 * 16      # 16 polls, 4 s of an unchanged empty composer after typing
        self.assertGreater(16 * 0.25, peerchat.VERIFY_TIMEOUT)
        self.assertLess(peerchat.SETTLE + 16 * 0.25, peerchat.verify_timeout(TEXT))
        fake = FakeWag(frames=[CODEX_IDLE] * (2 + late) + stable_frames(codex(TEXT), CODEX_IDLE))
        self.assertEqual('submitted', self.send(fake))
        self.assertEqual([TEXT, '\t'], fake.keys)

    def test_verify_timeout_grows_with_the_typed_text(self):
        self.assertEqual(peerchat.VERIFY_TIMEOUT, peerchat.verify_timeout(''))
        self.assertAlmostEqual(peerchat.VERIFY_TIMEOUT + 224 * peerchat.VERIFY_PER_CHAR,
                               peerchat.verify_timeout('x' * 224))

    def test_clipped_pointer_without_complete_id_never_authorizes_a_key(self):
        for visible in [TEXT[:30], TEXT[:TEXT.index('[id ')], TEXT[:TEXT.index(']')]]:
            with self.subTest(visible=visible):
                self.assertFalse(peerchat.owns(visible, TEXT))
                fake = FakeWag(frames=stable_frames(CODEX_IDLE, codex(visible)))
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                self.assertEqual([TEXT], fake.keys)

    def test_unlabelled_clipped_text_needs_at_least_half_the_message(self):
        text = 'An ordinary message without an id marker and with sufficient text to clip.'
        self.assertTrue(peerchat.owns(text, text))
        self.assertTrue(peerchat.owns(text[:len(text) // 2 + 1], text))
        self.assertFalse(peerchat.owns(text[:24], text))

    def test_captured_unsubmitted_frame_really_holds_a_pointer(self):
        content = peerchat.codex_composer(CODEX_UNSUBMITTED)
        self.assertIn('[id 20260922T221152Z-claude-7be1]', content)
        fake = FakeWag(frames=stable_frames(CODEX_IDLE, CODEX_UNSUBMITTED))
        with self.assertRaisesRegex(peerchat.Failed, 'pointer still unsent'):
            self.send(fake, content)
        self.assertEqual([content, '\t', '\t', '\t', '\n'], fake.keys)

    def test_codex_0158_context_footer_does_not_hide_the_typed_pointer(self):
        self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(CODEX_0158_IDLE))
        self.assertTrue(peerchat.owns(peerchat.codex_composer(CODEX_0158_TYPED), CODEX_0158_TEXT))
        fake = FakeWag(frames=stable_frames(CODEX_0158_IDLE, CODEX_0158_TYPED, CODEX_0158_IDLE))
        self.assertEqual('submitted', self.send(fake, CODEX_0158_TEXT))
        self.assertEqual([CODEX_0158_TEXT, '\t'], fake.keys)

    def test_numbered_chrome_row_is_not_stripped_at_the_right_edge(self):
        frame = CODEX_0158_TYPED.rsplit('\n', 1)[0] + '\n' + ' ' * 78 + '1. Yes'
        self.assertIsNone(peerchat.codex_composer(frame))

    def test_captured_context_footer_with_other_items_does_not_hide_the_typed_pointer(self):
        content = peerchat.codex_composer(CODEX_WARNINGS_FOOTER_TYPED)
        self.assertEqual(peerchat.compact(CODEX_WARNINGS_FOOTER_TEXT), peerchat.compact(content))
        fake = FakeWag(frames=stable_frames(CODEX_IDLE, CODEX_WARNINGS_FOOTER_TYPED, CODEX_IDLE))
        self.assertEqual('submitted', self.send(fake, CODEX_WARNINGS_FOOTER_TEXT))
        self.assertEqual([CODEX_WARNINGS_FOOTER_TEXT, '\t'], fake.keys)

    def test_context_footer_rejects_numbered_choice_rows(self):
        head = CODEX_WARNINGS_FOOTER_TYPED.rsplit('\n', 1)[0]
        for row in (' ' * 34 + '1. Yes · 59% context left',
                    ' ' * 34 + '1. Yes · Main · 59% context left'):
            with self.subTest(row=row):
                self.assertIsNone(peerchat.codex_composer(head + '\n' + row))

    def test_context_footer_with_other_items_below_a_wrapped_status(self):
        typed, status = CODEX_MULTILINE_STATUS_TYPED.split('\n\n')
        footer = CODEX_WARNINGS_FOOTER_TYPED.rsplit('\n', 1)[1]
        frame = typed + '\n\n' + status + '\n' + footer
        self.assertEqual(peerchat.codex_composer(CODEX_MULTILINE_STATUS_TYPED), peerchat.codex_composer(frame))

    def test_captured_idle_warnings_footer_allows_verified_submission(self):
        self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(CODEX_WARNINGS_FOOTER_IDLE))
        fake = FakeWag(frames=stable_frames(CODEX_WARNINGS_FOOTER_IDLE, CODEX_WARNINGS_FOOTER_TYPED,
                                            CODEX_WARNINGS_FOOTER_IDLE))
        self.assertEqual('submitted', self.send(fake, CODEX_WARNINGS_FOOTER_TEXT))
        self.assertEqual([CODEX_WARNINGS_FOOTER_TEXT, '\t'], fake.keys)

    def test_chrome_contents_do_not_determine_the_composer(self):
        # The human approved structural parsing after the Codex source check. These rows used
        # to fail content allowlists. Sending additionally requires the caret in the composer.
        for chrome in ('Unknown overlay', 'Approve this command?', 'GPT-new custom status',
                       '97% context left, 1. Yes', 'Main [default], 59% context left',
                       '59% context left ·', '59% context left · f2 to view · 1. Yes',
                       '59% context left · 2. No', '⚠ 2 warnings, f2 to view'):
            with self.subTest(chrome=chrome):
                idle = '› Ask Codex to do anything\n\n  ' + chrome
                typed = idle.replace('Ask Codex to do anything', TEXT)
                self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(idle))
                fake = FakeWag(frames=stable_frames(idle, typed, idle))
                self.assertEqual('submitted', self.send(fake))
                self.assertEqual([TEXT, '\t'], fake.keys)

    def test_captured_codex_multiline_status_allows_verified_submission(self):
        self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(CODEX_MULTILINE_STATUS))
        for footer in ('← for agents · ? for shortcuts', '? for shortcuts', '97% context left'):
            with self.subTest(footer=footer):
                idle = CODEX_MULTILINE_STATUS.replace('← for agents · ? for shortcuts', footer)
                typed = idle.replace('Ask Codex to do anything', TEXT[:70] + '\n  ' + TEXT[70:])
                fake = FakeWag(frames=stable_frames(idle, typed, idle))
                self.assertEqual('submitted', self.send(fake))
                self.assertEqual([TEXT, '\t'], fake.keys)

    def test_captured_codex_thread_id_status_allows_verified_submission(self):
        self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(CODEX_THREAD_STATUS))
        # With text in the composer this Codex drops the shortcuts footer and keeps the status row.
        typed = CODEX_THREAD_STATUS.replace('Ask Codex to do anything', TEXT).rsplit('\n', 1)[0]
        fake = FakeWag(frames=stable_frames(CODEX_THREAD_STATUS, typed, CODEX_THREAD_STATUS))
        self.assertEqual('submitted', self.send(fake))
        self.assertEqual([TEXT, '\t'], fake.keys)

    def test_captured_footerless_status_with_a_typed_pointer_is_verified(self):
        # Codex 0.170 drops the shortcuts footer once the composer holds text, so the wrapped
        # status block is the last block on screen.
        self.assertEqual('Ask Codex to do anything',
                         peerchat.codex_composer(CODEX_MULTILINE_STATUS.rsplit('\n', 1)[0]))
        expected = ' '.join(row.strip() for row in CODEX_MULTILINE_STATUS_TYPED.splitlines()[:3])[2:]
        self.assertEqual(expected, peerchat.codex_composer(CODEX_MULTILINE_STATUS_TYPED))
        fake = FakeWag(frames=stable_frames(CODEX_MULTILINE_STATUS, CODEX_MULTILINE_STATUS_TYPED,
                                            CODEX_MULTILINE_STATUS))
        self.assertEqual('submitted', self.send(fake, expected))
        self.assertEqual([expected, '\t'], fake.keys)
        frame = CODEX_MULTILINE_STATUS_TYPED.replace('  · 166K out', '  1. Yes\n  · 166K out')
        self.assertIsNone(peerchat.codex_composer(frame))

    def test_footerless_status_does_not_hide_an_unknown_block(self):
        status = CODEX_MULTILINE_STATUS_TYPED.split('\n\n')[1]
        frame = '› Ask Codex to do anything\n\n  Approve this command?\n  1. Yes\n  2. No\n\n' + status
        self.assertIsNone(peerchat.codex_composer(frame))

    def test_footerless_wrapped_pointer_keeps_its_last_row_and_allows_submit(self):
        prefix = '• Waiting for mail.\n\n'
        idle = prefix + '› Ask Codex to do anything'
        typed = prefix + CODEX_MULTILINE_STATUS_TYPED.split('\n\n')[0]
        expected = ' '.join(row.strip() for row in typed.splitlines()[2:])[2:]
        self.assertEqual(expected, peerchat.codex_composer(typed))
        fake = FakeWag(frames=stable_frames(idle, typed, idle))
        guard = peerchat.codex_cursor_in_composer
        def snapshot(*args, **kwargs):
            frame = typed if len(fake.keys) == 1 else idle
            row = len(frame.splitlines()) - 1
            return CodexCursorSnapshot.snapshot(frame, row, len(frame.splitlines()[row]) if len(fake.keys) == 1 else 2)
        with self.environment(fake), patch.object(peerchat, 'codex_cursor_in_composer', guard), \
                patch.object(wag, 'request', side_effect=snapshot):
            self.assertEqual('submitted', peerchat.send_once(
                'pane', peerchat.PROFILES['codex'], expected, dry_run=False))
        self.assertEqual([expected, '\t'], fake.keys)

    def test_multiline_status_keeps_drafts_and_shell_mode_refused(self):
        for content in ('an unsent draft', 'an unsent draft\n  wrapped continuation'):
            with self.subTest(content=content):
                frame = CODEX_MULTILINE_STATUS.replace('Ask Codex to do anything', content)
                self.assertEqual(content.replace('\n  ', ' '), peerchat.codex_composer(frame))
                fake = FakeWag(frames=[frame])
                with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'not empty'):
                    peerchat.precheck('pane', peerchat.PROFILES['codex'])
                self.assertEqual([], fake.keys)
        self.assertIsNone(peerchat.codex_composer(CODEX_MULTILINE_STATUS.replace('› ', '! ', 1)))

    def test_chrome_changes_preserve_the_composer(self):
        uuid = '01a1eee7-9789-7993-b57d-38256549f2d3'
        for frame in [
                CODEX_THREAD_STATUS.replace(uuid, 'Approve this command?'),
                CODEX_THREAD_STATUS.replace(uuid, uuid[:-1]),
                CODEX_THREAD_STATUS.replace(uuid, uuid.upper()),
                CODEX_MULTILINE_STATUS.replace('GPT-6.1-Sol high', 'Unknown overlay'),
                CODEX_MULTILINE_STATUS.replace('Main [default]', 'Approve this command?'),
                CODEX_MULTILINE_STATUS.replace('← for agents · ? for shortcuts', 'Choose a command'),
                CODEX_MULTILINE_STATUS + '\n  Approve this command?',
                CODEX_MULTILINE_STATUS.rsplit('\n', 1)[0].replace(' · Main [default]', ' ·')]:
            with self.subTest(frame=frame):
                self.assertEqual('Ask Codex to do anything', peerchat.codex_composer(frame))

    def test_chrome_is_bounded_and_never_contains_choice_or_prompt_rows(self):
        head = '› Ask Codex to do anything\n\n'
        self.assertEqual('Ask Codex to do anything',
                         peerchat.codex_composer(head + '\n'.join(['  status'] * 6)))
        for frame in [CODEX_MULTILINE_STATUS.replace('  29K out', '  1. Yes\n  29K out'),
                      head + '\n'.join(['  status'] * 7), head + '  › 1. Yes', head + '! shell',
                      head + '  ! shell', head + '  › another prompt']:
            with self.subTest(frame=frame):
                self.assertIsNone(peerchat.codex_composer(frame))
                fake = FakeWag(frames=[frame])
                with self.environment(fake), self.assertRaises(peerchat.Refused):
                    peerchat.precheck('pane', peerchat.PROFILES['codex'])
                self.assertEqual([], fake.keys)

    def test_multiline_status_does_not_hide_a_dialog_or_redraw(self):
        for frame in [CODEX_MULTILINE_STATUS + '\n› 1. Yes',
                      CODEX_MULTILINE_STATUS.replace('› Ask', '› 1. Yes\n› Ask')]:
            fake = FakeWag(frames=[frame])
            with self.environment(fake), self.assertRaises(peerchat.Refused):
                peerchat.precheck('pane', peerchat.PROFILES['codex'])
            self.assertEqual([], fake.keys)
        draft = CODEX_MULTILINE_STATUS.replace('Ask Codex to do anything', 'new draft')
        fake = FakeWag(frames=[CODEX_MULTILINE_STATUS, draft])
        with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'changed'):
            peerchat.precheck('pane', peerchat.PROFILES['codex'])
        self.assertEqual([], fake.keys)

    def test_dialog_and_disappearance_after_submit_withhold_further_keys(self):
        for frame, reason in [(codex(TEXT) + '\n› 1. Yes', 'dialog'), ('─' * 50, 'disappeared')]:
            with self.subTest(reason=reason):
                fake = FakeWag(after=lambda f: frame)
                with self.assertRaisesRegex(peerchat.Failed, reason):
                    self.send(fake)
                self.assertEqual([TEXT, '\t'], fake.keys)

    def test_fresh_retry_boundary_rejects_dialog_disappearance_or_new_draft(self):
        for changed in [codex(TEXT) + '\n› 1. Yes', '─' * 50, codex('another draft')]:
            with self.subTest(changed=changed):
                deadline_seen = False

                def after(fake):
                    nonlocal deadline_seen
                    if deadline_seen:
                        return changed
                    if self.clock.t >= 2 * peerchat.SETTLE + peerchat.SUBMIT_TIMEOUT:
                        deadline_seen = True
                    return codex(TEXT)

                self.clock.t = 0
                fake = FakeWag(after=after)
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                self.assertTrue(deadline_seen)
                self.assertEqual([TEXT, '\t'], fake.keys)

    def test_post_write_control_errors_are_failed_with_the_phase(self):
        cases = [('read', 3, 'verifying typed text'), ('read', 4, 'verifying typed text'),
                 ('read', 5, 'verifying submit'), ('read', 6, 'verifying submit'),
                 ('type', 1, 'typing text'), ('type', 2, 'submitting'), ('type', 3, 'retry 1'),
                 ('type', 5, 'Return fallback')]
        for kind, index, phase in cases:
            for error_type in [wag.CtlError, OSError]:
                with self.subTest(kind=kind, index=index, error_type=error_type):
                    fake = FakeWag(after=lambda f: codex(TEXT))
                    errors = fake.read_errors if kind == 'read' else fake.type_errors
                    errors[index] = error_type('lost terminal')
                    with self.assertRaisesRegex(peerchat.Failed, phase):
                        self.send(fake)

    def test_precheck_control_error_stays_prewrite(self):
        fake = FakeWag()
        fake.read_errors[1] = wag.CtlError('not reachable')
        with self.environment(fake), self.assertRaises(wag.CtlError):
            peerchat.send_once('pane', peerchat.PROFILES['codex'], TEXT, dry_run=False)
        self.assertEqual([], fake.keys)

    def test_dry_run_only_prechecks(self):
        fake = FakeWag()
        with self.environment(fake), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual('dry-run', peerchat.send_once('pane', peerchat.PROFILES['codex'], TEXT, dry_run=True))
        self.assertEqual([], fake.keys)
        self.assertEqual(2, fake.reads)

    def test_claude_classification_preserves_ambiguous_and_wrapped_drafts(self):
        for frame, cursor, expected in [
                (CLAUDE_IDLE, 2, 'empty'), (claude('Try "a suggestion"'), 2, 'empty'),
                (CLAUDE_SUGGESTION, 2, 'ambiguous'),
                (claude('a real draft with Home pressed'), 2, 'ambiguous'),
                (claude('a real draft'), 14, 'draft'), (CLAUDE_WRAPPED_DRAFT, 2, 'draft'),
                (claude('another draft\n'), 2, 'draft'),
                (CLAUDE_SUGGESTION, wag.CtlError('unsupported'), 'draft'),
                (CLAUDE_SUGGESTION, OSError('unavailable'), 'draft'),
                (CLAUDE_SUGGESTION.replace('\n> ', '\n  > '), 4, 'ambiguous')]:
            with self.subTest(cursor=cursor, expected=expected):
                fake = FakeWag('claude', frames=[frame], cursors=[cursor])
                with self.environment(fake):
                    text = wag.pane_text('pane')
                    content, state, _ = peerchat.composer_state('pane', peerchat.PROFILES['claude'], text)
                self.assertEqual(expected, state)
                self.assertEqual(peerchat.claude_composer(frame), content)
                if fake.cursor_reads:
                    self.assertEqual(['text', 'cursor', 'text'], fake.events)

    def test_ambiguous_and_draft_prechecks_never_type(self):
        for cursor, error, reason in [(2, peerchat.AmbiguousComposer, 'possibly a suggestion'),
                                      (30, peerchat.Refused, 'holds a draft')]:
            fake = FakeWag('claude', frames=[CLAUDE_SUGGESTION], cursors=[cursor])
            with self.environment(fake), self.assertRaisesRegex(error, reason) as caught:
                peerchat.send_once('pane', peerchat.PROFILES['claude'], TEXT, dry_run=False)
            self.assertEqual([], fake.keys)
            if error is peerchat.AmbiguousComposer:
                self.assertEqual(peerchat.claude_composer(CLAUDE_SUGGESTION), caught.exception.content)

    def test_dialog_still_refuses_even_with_cursor_at_start(self):
        fake = FakeWag('claude', frames=[CLAUDE_SUGGESTION + '\n> 1. Yes'], cursors=[2])
        with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'dialog'):
            peerchat.precheck('pane', peerchat.PROFILES['claude'])
        self.assertEqual([], fake.keys)
        self.assertEqual(0, fake.cursor_reads)

    def test_malformed_cursor_reply_keeps_text_only_refusal(self):
        cursor_reader = wag.cursor_column
        fake = FakeWag('claude', frames=[CLAUDE_SUGGESTION])
        with self.environment(fake), patch.object(wag, 'cursor_column', cursor_reader), \
                patch.object(wag, 'request', return_value={'column': 2}), \
                self.assertRaisesRegex(peerchat.Refused, 'holds a draft'):
            peerchat.precheck('pane', peerchat.PROFILES['claude'])
        self.assertEqual([], fake.keys)

    def test_suggestion_only_after_typing_waits_then_withholds_submit(self):
        fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE, CLAUDE_SUGGESTION), cursors=[2])
        with self.assertRaisesRegex(peerchat.Failed, 'typed text was not confirmed'):
            self.send(fake)
        self.assertEqual([TEXT], fake.keys)
        self.assertGreater(fake.cursor_reads, 1)
        self.assertGreaterEqual(self.clock.t, peerchat.SETTLE + peerchat.verify_timeout(TEXT))
        self.assertLess(self.clock.t, peerchat.SETTLE + peerchat.verify_timeout(TEXT) + .25)

    def test_post_submit_ambiguity_waits_but_does_not_prove_success(self):
        for other in [CLAUDE_SUGGESTION, claude(TEXT[:30])]:
            with self.subTest(other=other):
                self.clock.t = 0
                fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE, claude(TEXT), other), cursors=[2])
                with self.assertRaisesRegex(peerchat.Failed, 'submit not proven'):
                    self.send(fake)
                self.assertEqual([TEXT, '\n'], fake.keys)
                self.assertGreater(fake.cursor_reads, 2)
                self.assertAlmostEqual(2 * peerchat.SETTLE + peerchat.SUBMIT_TIMEOUT, self.clock.t)

    def test_ambiguity_can_resolve_to_empty_within_submit_window(self):
        fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE, claude(TEXT), CLAUDE_SUGGESTION, CLAUDE_IDLE), cursors=[2])
        self.assertEqual('submitted', self.send(fake))
        self.assertEqual([TEXT, '\n'], fake.keys)

    def test_own_pointer_at_start_still_uses_only_existing_key_retries(self):
        fake = FakeWag('claude', after=lambda f: claude(TEXT), cursors=[2])
        with self.assertRaisesRegex(peerchat.Failed, 'pointer still unsent'):
            self.send(fake)
        self.assertEqual([TEXT, '\n', '\n', '\n'], fake.keys)

    def test_codex_does_not_read_cursor_or_gain_ambiguity(self):
        fake = FakeWag('codex', frames=[codex('a real draft')], cursors=[2])
        with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'not empty'):
            peerchat.precheck('pane', peerchat.PROFILES['codex'])
        self.assertEqual(0, fake.cursor_reads)
        self.assertEqual([], fake.keys)

    def test_codex_cursor_outside_composer_withholds_typing(self):
        fake = FakeWag(frames=[CODEX_WARNINGS_FOOTER_IDLE])
        with self.environment(fake), patch.object(peerchat, 'codex_cursor_in_composer', return_value=False):
            with self.assertRaisesRegex(peerchat.Refused, 'composer visible but caret not in it'):
                peerchat.precheck('pane', peerchat.PROFILES['codex'])
        self.assertEqual([], fake.keys)

    def test_codex_cursor_leaving_composer_after_typing_withholds_submit(self):
        fake = FakeWag(frames=stable_frames(CODEX_WARNINGS_FOOTER_IDLE, CODEX_WARNINGS_FOOTER_TYPED))
        with self.environment(fake), patch.object(peerchat, 'codex_cursor_in_composer',
                side_effect=lambda pane, content: content == 'Ask Codex to do anything'):
            with self.assertRaisesRegex(peerchat.Failed, 'submit withheld'):
                peerchat.send_once('pane', peerchat.PROFILES['codex'], CODEX_WARNINGS_FOOTER_TEXT, dry_run=False)
        self.assertEqual([CODEX_WARNINGS_FOOTER_TEXT], fake.keys)

    def test_codex_empty_composer_after_submit_proves_delivery_when_caret_leaves(self):
        fake = FakeWag(frames=stable_frames(CODEX_WARNINGS_FOOTER_IDLE, CODEX_WARNINGS_FOOTER_TYPED,
                                            CODEX_WARNINGS_FOOTER_IDLE))
        with self.environment(fake), patch.object(peerchat, 'codex_cursor_in_composer',
                side_effect=lambda pane, content: len(fake.keys) < 2):
            self.assertEqual('submitted', peerchat.send_once(
                'pane', peerchat.PROFILES['codex'], CODEX_WARNINGS_FOOTER_TEXT, dry_run=False))
        self.assertEqual([CODEX_WARNINGS_FOOTER_TEXT, '\t'], fake.keys)

    def test_codex_pointer_after_submit_with_caret_elsewhere_never_gets_retry_keys(self):
        fake = FakeWag(frames=stable_frames(CODEX_WARNINGS_FOOTER_IDLE, CODEX_WARNINGS_FOOTER_TYPED,
                                            CODEX_WARNINGS_FOOTER_TYPED))
        with self.environment(fake), patch.object(peerchat, 'codex_cursor_in_composer',
                side_effect=lambda pane, content: len(fake.keys) < 2):
            with self.assertRaisesRegex(peerchat.Failed, 'composer visible but caret not in it'):
                peerchat.send_once('pane', peerchat.PROFILES['codex'], CODEX_WARNINGS_FOOTER_TEXT, dry_run=False)
        self.assertEqual([CODEX_WARNINGS_FOOTER_TEXT, '\t'], fake.keys)

    def test_codex_bare_prompt_after_submit_waits_for_its_placeholder_without_extra_keys(self):
        fake = FakeWag(frames=stable_frames(CODEX_IDLE, codex(TEXT), '› ', CODEX_IDLE))
        self.assertEqual('submitted', self.send(fake))
        self.assertEqual([TEXT, '\t'], fake.keys)

    def test_precheck_requires_two_equal_empty_snapshots(self):
        for tool, idle, render in [('claude', CLAUDE_IDLE, claude), ('codex', CODEX_IDLE, codex)]:
            with self.subTest(tool=tool):
                fake = FakeWag(tool, frames=[idle, render('new draft')], cursors=[2])
                with self.environment(fake), self.assertRaisesRegex(peerchat.Refused, 'changed'):
                    peerchat.send_once('pane', peerchat.PROFILES[tool], TEXT, dry_run=False)
                self.assertEqual([], fake.keys)
                self.assertEqual(2, fake.reads)

    def test_redraw_during_cursor_read_withholds_first_submit(self):
        fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE) + [claude(TEXT), claude('new draft')], cursors=[2])
        with self.assertRaises(peerchat.Failed):
            self.send(fake)
        self.assertEqual([TEXT], fake.keys)
        self.assertEqual(['text', 'cursor', 'text'], fake.events[3:6])

    def test_changed_geometry_waits_for_a_stable_poll_before_first_submit(self):
        wrapped = claude(TEXT.replace(' ', '\n', 1))
        self.assertEqual(peerchat.claude_composer(claude(TEXT)), peerchat.claude_composer(wrapped))
        fake = FakeWag('claude', frames=stable_frames(CLAUDE_IDLE) + [claude(TEXT), wrapped] +
                      stable_frames(wrapped, CLAUDE_IDLE), cursors=[2])
        self.assertEqual('submitted', self.send(fake))
        self.assertEqual([TEXT, '\n'], fake.keys)
        self.assertAlmostEqual(2 * peerchat.SETTLE + .25, self.clock.t)

    def test_second_snapshot_dialog_withholds_typing_and_submit(self):
        for after_typing in [False, True]:
            with self.subTest(after_typing=after_typing):
                frames = stable_frames(CLAUDE_IDLE) if after_typing else []
                frame = claude(TEXT) if after_typing else CLAUDE_IDLE
                fake = FakeWag('claude', frames=frames + [frame, frame + '\n> 1. Yes'], cursors=[2])
                with self.environment(fake), self.assertRaisesRegex(peerchat.Failed if after_typing else peerchat.Refused, 'dialog'):
                    peerchat.send_once('pane', peerchat.PROFILES['claude'], TEXT, dry_run=False)
                self.assertEqual([TEXT] if after_typing else [], fake.keys)

    def test_redraw_at_retry_boundary_withholds_retry_key(self):
        for tool, render, key in [('claude', claude, '\n'), ('codex', codex, '\t')]:
            with self.subTest(tool=tool):
                self.clock.t = 0
                boundary_reads = 0
                def after(fake):
                    nonlocal boundary_reads
                    if self.clock.t >= 2 * peerchat.SETTLE + peerchat.SUBMIT_TIMEOUT:
                        boundary_reads += 1
                        if boundary_reads >= 4:
                            return render('new draft')
                    return render(TEXT)
                fake = FakeWag(tool, after=after, cursors=[2])
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                self.assertEqual([TEXT, key], fake.keys)
                self.assertEqual(4, boundary_reads)

    def test_redraw_at_return_fallback_withholds_return(self):
        for changed in [codex('new draft'), codex(TEXT, 'Working (esc to interrupt)')]:
            with self.subTest(changed=changed):
                self.clock.t = 0
                started = None
                boundary_reads = 0
                def after(fake):
                    nonlocal started, boundary_reads
                    if len(fake.keys) == 4:
                        if started is None:
                            started = self.clock.t
                        if self.clock.t >= started + peerchat.SUBMIT_TIMEOUT:
                            boundary_reads += 1
                            if boundary_reads >= 4:
                                return changed
                    return codex(TEXT)
                fake = FakeWag(after=after)
                with self.assertRaises(peerchat.Failed):
                    self.send(fake)
                self.assertEqual([TEXT, '\t', '\t', '\t'], fake.keys)
                self.assertEqual(4, boundary_reads)

    def test_cli_prints_valid_json(self):
        fake = FakeWag()
        output = io.StringIO()
        with self.environment(fake), patch.object(sys, 'argv', ['peerchat', '--to', 'codex', '--text', 'hello', '--label', '']), \
                patch.object(peerchat, 'resolve_target', return_value=('pane', peerchat.PROFILES['codex'], 'codex')), \
                patch.object(wag, 'my_pane', return_value='other'), contextlib.redirect_stdout(output):
            self.assertEqual(0, peerchat.main())
        self.assertEqual({'sent': 'submitted', 'to': 'codex', 'pane': 'pane'}, json.loads(output.getvalue()))

    def test_dry_run_cli_stdout_is_json_and_diagnostics_are_stderr(self):
        fake = FakeWag()
        output, diagnostics = io.StringIO(), io.StringIO()
        with self.environment(fake), patch.object(sys, 'argv', ['peerchat', '--to', 'codex', '--text', 'hello', '--label', '', '--dry-run']), \
                patch.object(peerchat, 'resolve_target', return_value=('pane', peerchat.PROFILES['codex'], 'codex')), \
                patch.object(wag, 'my_pane', return_value='other'), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(diagnostics):
            self.assertEqual(0, peerchat.main())
        self.assertEqual({'sent': 'dry-run', 'to': 'codex', 'pane': 'pane'}, json.loads(output.getvalue()))
        self.assertIn('[dry-run] would type', diagnostics.getvalue())
        self.assertEqual([], fake.keys)
        self.assertEqual(2, fake.reads)


class CodexCursorSnapshot(unittest.TestCase):
    @staticmethod
    def snapshot(frame, row, column=2):
        return {'cols': 240, 'cursor': {'row': row, 'col': column, 'visible': True}, 'rows': [
            {'row': index, 'runs': [{'col': 0, 'width': len(text), 'text': text}]}
            for index, text in enumerate(frame.splitlines())]}

    def test_caret_inside_idle_or_wrapped_typed_composer(self):
        for frame, row in [(CODEX_WARNINGS_FOOTER_IDLE, 0), (CODEX_WARNINGS_FOOTER_TYPED, 0),
                           (CODEX_WARNINGS_FOOTER_TYPED, 2)]:
            with self.subTest(row=row, frame=frame):
                snapshot = self.snapshot(frame, row)
                with patch.object(wag, 'request', return_value=snapshot) as request:
                    self.assertTrue(peerchat.codex_cursor_in_composer('pane', peerchat.codex_composer(frame)))
                request.assert_called_once_with('session.text', target='pane', args={'styles': True}, timeout=15.0)

    def test_history_search_footer_and_centered_dialog_caret_are_outside_composer(self):
        frame = '  Dialog input\n\n› Ask Codex to do anything\n\n  status\n  Search history: '
        for row, column in [(0, 14), (4, 2), (5, 18), (2, 20), (99, 2)]:
            with self.subTest(row=row, column=column), patch.object(wag, 'request',
                    return_value=self.snapshot(frame, row, column)):
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_row_coordinates_survive_long_transcripts_and_trailing_blank_rows(self):
        frame = 'old output\n' * 45 + CODEX_WARNINGS_FOOTER_IDLE + '\n\n\n'
        with patch.object(wag, 'request', return_value=self.snapshot(frame, 45)):
            self.assertTrue(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_unicode_separator_cells_do_not_shift_styled_cursor_rows(self):
        for separator in ('\u2028', '\u2029', '\x85', '\x1c'):
            lines = ['old' + separator + 'output', '', '› Ask Codex to do anything', '', '  status']
            snapshot = {'cols': 100, 'cursor': {'row': 2, 'col': 2, 'visible': True}, 'rows': [
                {'row': index, 'runs': [{'col': 0, 'width': len(text), 'text': text}]}
                for index, text in enumerate(lines)]}
            with self.subTest(separator=separator), patch.object(wag, 'request', return_value=snapshot):
                self.assertTrue(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))
                snapshot['cursor']['row'] = 3
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_changed_composer_snapshot_cannot_authorize_old_content(self):
        for frame in ['› another draft\n\n  status', '! shell\n\n  status', '  No composer']:
            with self.subTest(frame=frame), patch.object(wag, 'request', return_value=self.snapshot(frame, 0)):
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_gaps_between_styled_runs_use_cell_columns(self):
        snapshot = self.snapshot('› Ask Codex to do anything\n\n  status', 0)
        snapshot['rows'][0]['runs'] = [
            {'col': 0, 'width': 1, 'text': '›'},
            {'col': 2, 'width': 23, 'text': 'Ask Codex to do anything'}]
        with patch.object(wag, 'request', return_value=snapshot):
            self.assertTrue(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_hidden_or_missing_visibility_never_authorizes_stale_composer_coordinates(self):
        snapshot = self.snapshot('› Ask Codex to do anything\n\n  Rename thread: name', 0)
        for visible in [False, None, 0, 1, 'true']:
            with self.subTest(visible=visible):
                snapshot['cursor']['visible'] = visible
                with patch.object(wag, 'request', return_value=snapshot):
                    self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))
        del snapshot['cursor']['visible']
        with patch.object(wag, 'request', return_value=snapshot):
            self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))

    def test_malformed_or_missing_cursor_evidence_fails_closed(self):
        good = self.snapshot('› Ask Codex to do anything\n\n  status', 0)
        for value in [None, [], {}, {'cursor': None}, {**good, 'rows': None},
                      {**good, 'cols': None}, {**good, 'cols': True}, {**good, 'cols': 0},
                      {**good, 'rows': [{'row': 2, 'runs': []}]},
                      {**good, 'rows': [{'row': 0, 'runs': [{'col': -1, 'width': 1, 'text': 'x'}]}]},
                      {**good, 'rows': [{'row': 0, 'runs': [{'col': 2**70, 'width': 1, 'text': 'x'}]}]},
                      {**good, 'rows': [{'row': 0, 'runs': [{'col': 239, 'width': 2, 'text': 'x'}]}]}]:
            with self.subTest(value=value), patch.object(wag, 'request', return_value=value):
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))
        for row, column in [(None, 2), (True, 2), (-1, 2), (0, None), (0, True), (0, -1)]:
            with self.subTest(row=row, column=column), patch.object(wag, 'request',
                    return_value=self.snapshot('› Ask Codex to do anything', row, column)):
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))
        for error in [wag.CtlError('unsupported'), OSError('unreachable')]:
            with self.subTest(error=error), patch.object(wag, 'request', side_effect=error):
                self.assertFalse(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))


class CursorTransport(unittest.TestCase):
    def test_cli_fallback_preserves_styles_and_the_real_codex_guard(self):
        snapshot = CodexCursorSnapshot.snapshot(CODEX_WARNINGS_FOOTER_IDLE, 0)
        response = subprocess.CompletedProcess([], 0, json.dumps({'ok': True, 'result': snapshot}).encode(), b'')
        with patch.object(wag, '_via_pipe', return_value=None), \
                patch.object(wag, 'ctl_path', return_value='stub-ctl'), \
                patch.object(wag, '_console_utf8', return_value=lambda: None), \
                patch.object(wag.subprocess, 'run', return_value=response) as run:
            self.assertTrue(peerchat.codex_cursor_in_composer('pane', 'Ask Codex to do anything'))
        self.assertEqual(['stub-ctl', 'session', 'text', '--styles', '--target', 'pane', '--json'], run.call_args.args[0])

    def test_non_object_envelopes_on_pipe_are_refused_without_cli_fallback(self):
        for raw in [b'[]', b'null', b'"text"']:
            with self.subTest(raw=raw), patch.object(wag, '_open_pipe', return_value=io.BytesIO()), \
                    patch.object(wag, '_pipe_roundtrip', return_value=raw), \
                    patch.object(wag, '_via_cli', side_effect=AssertionError('invalid pipe reply must not fall back')), \
                    self.assertRaisesRegex(wag.CtlError, 'non-object envelope'):
                wag.request('surface.cursor', target='pane')

    def test_non_object_envelopes_on_cli_are_control_errors(self):
        for raw in [b'[]', b'null', b'"text"']:
            with self.subTest(raw=raw), patch.object(wag, '_open_pipe', return_value=None), \
                    patch.object(wag, 'ctl_path', return_value='stub-ctl'), \
                    patch.object(wag, '_console_utf8', return_value=lambda: None), \
                    patch.object(wag.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, raw, b'')), \
                    self.assertRaisesRegex(wag.CtlError, 'non-object envelope'):
                wag.request('surface.cursor', target='pane')

    def test_decimal_conversion_limit_is_reported_as_control_error(self):
        previous = sys.get_int_max_str_digits()
        limit = sys.int_info.str_digits_check_threshold
        self.addCleanup(sys.set_int_max_str_digits, previous)
        sys.set_int_max_str_digits(limit)
        value = '1' * (limit + 1)
        self.assertTrue(value.isascii() and value.isdecimal())
        with self.assertRaises(ValueError):
            int(value)
        with patch.object(wag, 'request', return_value=value), self.assertRaises(wag.CtlError):
            wag.cursor_column('pane')

    def test_pipe_accepts_integer_or_decimal_string(self):
        for value in [0, 2, '2', ' 2\n', 80]:
            with self.subTest(value=value), patch.object(wag, '_via_pipe', return_value={'ok': True, 'result': value}) as pipe, \
                    patch.object(wag, '_via_cli', side_effect=AssertionError('unexpected CLI')):
                self.assertEqual(int(value), wag.cursor_column('pane'))
                self.assertEqual({'cmd': 'surface.cursor', 'target': 'pane'}, pipe.call_args.args[0])

    def test_refused_and_malformed_replies_are_control_errors(self):
        replies = [{'ok': False, 'error': 'unsupported'}]
        replies += [{'ok': True, 'result': value} for value in
                    [None, True, False, -1, 2.0, '2.0', '-1', '', 'column 2', {}, [2]]]
        for reply in replies:
            with self.subTest(reply=reply), patch.object(wag, '_via_pipe', return_value=reply), \
                    self.assertRaises(wag.CtlError):
                wag.cursor_column('pane')

    def test_cli_fallback_keeps_cursor_verb_target_and_json_envelope(self):
        response = subprocess.CompletedProcess([], 0, b'{"ok":true,"result":2}', b'')
        with patch.object(wag, '_via_pipe', return_value=None), \
                patch.object(wag, 'ctl_path', return_value='stub-ctl'), \
                patch.object(wag, '_console_utf8', return_value=lambda: None), \
                patch.object(wag.subprocess, 'run', return_value=response) as run:
            self.assertEqual(2, wag.cursor_column('pane'))
        self.assertEqual(['stub-ctl', 'surface', 'cursor', '--target', 'pane', '--json'], run.call_args.args[0])


class CapturedBusyFrames(unittest.TestCase):
    def test_activity_matches_captured_frames(self):
        for frame, expected in [(CLAUDE_IDLE, False), (claude('check if codex replied'), False),
                                (CLAUDE_RUNNING, True), (CODEX_IDLE, False), (CODEX_QUEUED, True),
                                (CODEX_UNSUBMITTED, False)]:
            with self.subTest(frame=frame):
                self.assertEqual(expected, peerchat.is_busy(frame))

    def test_claude_spinner_remains_busy_above_a_tall_tip_block(self):
        frame = CLAUDE_RUNNING.replace('\n  ⎿', '\n' + '  more tip details\n' * 20 + '  ⎿')
        self.assertNotIn('Hullaballooing', '\n'.join(frame.splitlines()[-15:]))
        self.assertTrue(relay.is_busy(frame))

    def test_claude_hours_and_upload_phase_are_busy(self):
        for activity in ['1h 2m 3s · ↓ 4.9k tokens', '45s · ↑ 1.2k tokens', '2m 30s · thinking']:
            with self.subTest(activity=activity):
                frame = CLAUDE_RUNNING.replace('2m 30s · ↓ 4.9k tokens', activity)
                self.assertTrue(relay.is_busy(frame))
        self.assertFalse(relay.is_busy(CLAUDE_IDLE))


KIMI_FRAMES = Path(__file__).resolve().parent / 'fixtures' / 'kimi'


def kimi_frame(name):
    """A frame captured from a live Kimi Code 2.1.1 pane in wagterm (see fixtures/kimi/README.md)."""
    return (KIMI_FRAMES / f'{name}.txt').read_text(encoding='utf-8')


def kimi(content, frame='idle-after-turn'):
    """The captured idle frame with `content` in its composer's `>` row."""
    lines = kimi_frame(frame).splitlines()
    row = next(i for i, line in enumerate(lines) if line.lstrip().startswith('│ >'))
    lines[row] = ' │ > ' + content + ' │'
    return '\n'.join(lines)


class KimiComposer(unittest.TestCase):
    """#65: Kimi Code's composer, read from frames captured in wagterm (UTF-8 console)."""

    def test_captured_frames_classify(self):
        cases = {
            'idle-fresh': '', 'idle-after-turn': '', 'status-error': '',
            'running-thinking': '', 'running-tool': '',
            'draft-wrapped': ('Run the shell command: sleep 25 && echo slept. Then reply with exactly the word '
                              'done. This sentence pads the draft so that it wraps across more than one row of '
                              'the composer box, which is what the parser must join back together correctly '
                              'without losing any text at all.'),
            # A Return typed in the same burst as the text became a newline: an empty wrapped row.
            'burst': 'reply with the single word ok please',
            'approval': None, 'trust-dialog': None, 'shell-mode': None,
        }
        for name, expected in cases.items():
            with self.subTest(frame=name):
                self.assertEqual(expected, peerchat.kimi_composer(kimi_frame(name)))
                self.assertEqual(expected, peerchat.composer_content('kimi', kimi_frame(name)))

    def test_running_turns_are_busy_and_idle_ones_are_not(self):
        for name, expected in [('running-thinking', True), ('running-tool', True), ('idle-fresh', False),
                               ('idle-after-turn', False), ('status-error', False), ('draft-wrapped', False)]:
            with self.subTest(frame=name):
                self.assertEqual(expected, peerchat.is_busy(kimi_frame(name)))
                self.assertEqual(expected, relay.is_busy(kimi_frame(name)))
        retrying = kimi_frame('running-thinking').replace('⠹ Thinking… · Tip:', '⠼ Retrying (2/10) · rate_limit · in 4s ·')
        self.assertTrue(peerchat.is_busy(retrying))
        # The other tools' frames never look like a Kimi turn.
        self.assertFalse(peerchat.kimi_busy(CLAUDE_IDLE) or peerchat.kimi_busy(CODEX_IDLE))

    def test_the_todo_panel_between_the_spinner_and_the_box(self):
        # #88 reopened: Kimi docks its todo panel under the spinner, right over the composer box.
        def docked(name, todos):
            head, _, box = kimi_frame(name).rpartition('\n ╭')     # the composer, not the welcome panel
            return head + '\n' + '\n'.join([' ' + '─' * 40, '   Todo', *todos]) + '\n ╭' + box
        collapsed = ['   ● Running tests', '   ○ Commit', '   … +2 more (2 done) · ctrl+t to expand']
        expanded = [f'   ○ step {n}' for n in range(40)] + ['   all 40 items · ctrl+t to collapse']
        for todos in (collapsed, expanded):
            with self.subTest(rows=len(todos)):
                for name, expected in [('running-thinking', True), ('running-tool', True),
                                       ('idle-after-turn', False), ('status-error', False)]:
                    self.assertEqual(expected, peerchat.kimi_busy(docked(name, todos)), name)
                    self.assertEqual(expected, peerchat.is_busy(docked(name, todos)), name)
                self.assertEqual('', peerchat.kimi_composer(docked('idle-after-turn', todos)))

    def test_dialogs_are_dialogs(self):
        for name, expected in [('approval', True), ('trust-dialog', True), ('idle-after-turn', False),
                               ('running-tool', False), ('draft-wrapped', False)]:
            with self.subTest(frame=name):
                self.assertEqual(expected, peerchat.dialog_visible(kimi_frame(name)))

    def test_a_box_that_is_not_at_the_bottom_or_not_a_composer_is_missing(self):
        idle = kimi_frame('idle-after-turn')
        self.assertIsNone(peerchat.kimi_composer(idle + '\n' + '\n'.join(f'row {n}' for n in range(5))))
        # The welcome panel is a box too, but its first row is not the `>` row.
        welcome = kimi_frame('idle-fresh').split('✦')[0]
        self.assertIsNone(peerchat.kimi_composer(welcome))
        self.assertIsNone(peerchat.kimi_composer(idle.replace(' │ >', ' │ ! echo x', 1)))
        unknown = idle.replace(' │ >', ' │ >\n │ 1. Yes │', 1)
        self.assertIsNone(peerchat.kimi_composer(unknown))


class KimiSubmission(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def send(self, fake, text=TEXT):
        stamps = []
        typed = fake.type_into

        def type_into(pane, keys):
            stamps.append((self.clock.now(), keys))
            typed(pane, keys)

        with patch.object(wag, 'pane_text', fake.pane_text), patch.object(wag, 'type_into', type_into), \
                patch.object(wag, 'cursor_column', fake.cursor_column), \
                patch.object(wag, 'request', side_effect=AssertionError('real terminal request')), \
                patch.object(peerchat, 'now', self.clock.now), patch.object(peerchat, 'pause', self.clock.pause):
            return peerchat.send('pane', peerchat.PROFILES['kimi'], text, dry_run=False, retry=False), stamps

    def fake(self, **kwargs):
        idle = kimi_frame('idle-after-turn')

        class Fake(FakeWag):
            def pane_text(inner, pane):
                inner.reads += 1
                if inner.frames:
                    return inner.frames.pop(0) if len(inner.frames) > 1 else inner.frames[0]
                return kimi(inner.keys[0]) if len(inner.keys) == 1 else idle

        return Fake('kimi', **kwargs)

    def test_text_then_return_well_after_the_paste_burst_window(self):
        fake = self.fake()
        outcome, stamps = self.send(fake)
        self.assertEqual('submitted', outcome)
        self.assertEqual([TEXT, '\n'], fake.keys)
        # A Return within 120 ms of a typed burst becomes a newline in Kimi's composer (captured in
        # fixtures/kimi/burst.txt); the key goes in its own call, at least 150 ms after the text.
        self.assertGreaterEqual(stamps[1][0] - stamps[0][0], 0.15)
        self.assertEqual("\n", peerchat.PROFILES['kimi'].submit)

    def test_running_dialog_draft_and_shell_frames_are_refused_before_typing(self):
        for name, frame, reason in [
                ('running', kimi_frame('running-tool'), None),
                ('approval', kimi_frame('approval'), 'dialog'),
                ('trust', kimi_frame('trust-dialog'), 'dialog'),
                ('draft', kimi_frame('draft-wrapped'), 'not empty'),
                ('shell', kimi_frame('shell-mode'), 'no Kimi composer')]:
            with self.subTest(frame=name):
                fake = self.fake(frames=[frame])
                if name == 'running':
                    # peerchat itself only proves an empty box; the relay refuses a running turn first.
                    self.assertTrue(relay.is_busy(frame))
                    continue
                with self.assertRaises(peerchat.Refused) as caught:
                    self.send(fake)
                self.assertIn(reason, str(caught.exception))
                self.assertEqual([], fake.keys)
