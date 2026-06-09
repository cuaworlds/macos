#!/bin/bash
# One-time prep of the dockur/macos base volume so cloned guests are harness-ready.
# Run INSIDE the guest (over SSH as the `user` account) on a container backed by the
# volume you intend to use as the gold image, then shut that container down cleanly.
#
#   ssh -i id_kvm -p <port> user@<host> 'bash -s' < prep_base.sh haime
#
# Idempotent. Does four things:
#   1. Symlink /Users/ec2-user -> /Users/user so the MacOSWorld tasks' absolute
#      /Users/ec2-user/... paths resolve to the real home (the managed backend has a
#      real ec2-user account; this bridges the gap without renaming the account).
#   2. Enable auto-login for `user` (kcpassword + loginwindow) so a fresh clone boots
#      straight to the desktop instead of the login window.
#   3. Disable the idle screensaver/lock so a rollout never gets locked out mid-task.
#   4. Warm the built-in app data stores (Notes/Reminders/Calendar/Mail) so the
#      osascript grading queries hit initialised databases.
set -u
PASSWORD="${1:-haime}"
USER_NAME="user"

echo "== 1. symlink /Users/ec2-user -> /Users/$USER_NAME =="
sudo ln -sfn "/Users/$USER_NAME" /Users/ec2-user
ls -ld /Users/ec2-user

echo "== 2. enable auto-login for $USER_NAME =="
# /etc/kcpassword holds the obfuscated password (XOR with Apple's rolling key).
# Use perl, not python3 — the macOS python3 shim only triggers the Xcode CLT installer.
sudo /usr/bin/perl - "$PASSWORD" <<'PL'
my @key = (0x7D, 0x89, 0x52, 0x23, 0x06, 0x44, 0xBB, 0x00);
my @b = unpack("C*", $ARGV[0]);
my $pad = (scalar(@b) % 12) ? (12 - scalar(@b) % 12) : 12;
push @b, (0) x $pad;
my @out = map { $b[$_] ^ $key[$_ % 8] } 0 .. $#b;
open(my $f, ">", "/etc/kcpassword") or die "$!";
binmode $f;
print $f pack("C*", @out);
close $f;
PL
sudo chmod 600 /etc/kcpassword
sudo chown root:wheel /etc/kcpassword
sudo defaults write /Library/Preferences/com.apple.loginwindow autoLoginUser "$USER_NAME"
sudo defaults write /Library/Preferences/com.apple.loginwindow DisableFDEAutoLogin -bool false
echo "autoLoginUser -> $(sudo defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser)"

echo "== 3. disable idle screensaver/lock =="
defaults -currentHost write com.apple.screensaver idleTime -int 0
sudo defaults write /Library/Preferences/com.apple.screensaver loginWindowIdleTime -int 0 2>/dev/null || true
defaults write com.apple.screensaver askForPassword -int 0 2>/dev/null || true

echo "== 4. warm built-in app data stores =="
for app in Notes Reminders Calendar Mail; do
  osascript -e "tell application \"$app\" to launch" 2>/dev/null || true
done
sleep 8
for app in Notes Reminders Calendar Mail; do
  osascript -e "tell application \"$app\" to quit" 2>/dev/null || true
done

echo "== prep complete =="
