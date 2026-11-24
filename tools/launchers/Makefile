# make -C tools/launchers <target>
.PHONY: webui-up webui-down webui-native webui-fake test
webui-up:      ; ./webui/run.sh start
webui-down:    ; ./webui/run.sh stop
webui-native:  ; ./webui/run.sh native
webui-fake:    ; ./webui/run.sh fake
test:          ; ../../.venv/bin/python -m pytest ../../tests/launchers -q || python3 -m pytest ../../tests/launchers -q
