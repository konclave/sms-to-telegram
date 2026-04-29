#!/bin/bash

ln -sf /home/konclave/Projects/sms-to-telegram/sms-to-telegram.container /etc/containers/systemd/sms-to-telegram.container
systemctl daemon-reload
systemctl stop sms-to-telegram
systemctl start sms-to-telegram
