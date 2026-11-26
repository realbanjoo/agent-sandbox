.PHONY: check
check:
	python3 -m py_compile gate/lib/*.py sandbox/lib/*.py hetzner/lib/*.py
	bash -n sandbox/bin/sandbox-run sandbox/root/sandbox-root
	cd tests && python3 -B -m unittest discover -s . -v
