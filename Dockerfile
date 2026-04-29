FROM ubuntu:focal
ENV PIN 0000
RUN apt-get update && \
    apt-get install --no-install-recommends -y gammu-smsd curl gettext locales ca-certificates python3 && \
		localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 && \
		apt-get clean && apt-get autoclean && \
		rm -rf /var/lib/apt/lists/*
ENV LC_ALL en_US.UTF-8
ENV PYTHONPATH=/opt/sms_forwarder
COPY gammurc /etc/gammurc
COPY entrypoint.sh /usr/bin/entrypoint.sh
COPY enqueue_sms.py send_worker.py check_forwarder_health.py /usr/bin/
COPY sms_forwarder /opt/sms_forwarder/sms_forwarder
RUN mkdir -p \
        /var/log/smsd/ \
        /var/spool/gammu/inbox \
        /var/spool/gammu/outbox \
        /var/spool/gammu/sent \
        /var/spool/gammu/error \
        /var/spool/sms-forwarder/pending \
        /var/spool/sms-forwarder/processing \
        /var/spool/sms-forwarder/sent \
        /var/spool/sms-forwarder/failed && \
    chmod +x /usr/bin/entrypoint.sh /usr/bin/enqueue_sms.py /usr/bin/send_worker.py /usr/bin/check_forwarder_health.py

HEALTHCHECK CMD /usr/bin/check_forwarder_health.py
ENTRYPOINT ["entrypoint.sh"]
