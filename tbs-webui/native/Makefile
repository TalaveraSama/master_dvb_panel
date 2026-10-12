# Compila la criptografia Newcamd (port de OSCam) como biblioteca compartida.
#
#   make            -> compila tbs_newcamd.so en este directorio
#   make install    -> instala en /usr/local/lib y ejecuta ldconfig
#   make test       -> autoprueba con un vector conocido (nc_selftest)
#
# La biblioteca se carga desde Python con ctypes (app/services/newcamd_crypto.py).

CC      ?= cc
CFLAGS  ?= -O2 -fPIC -Wall -Wextra -std=c99
LDFLAGS ?= -shared
PREFIX  ?= /usr/local
LIBDIR  ?= $(PREFIX)/lib

LIB     := tbs_newcamd.so
OBJS    := tbs_newcamd.o

.PHONY: all install test clean

all: $(LIB)

$(LIB): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $(OBJS)

tbs_newcamd.o: tbs_newcamd.c tbs_newcamd.h
	$(CC) $(CFLAGS) -c tbs_newcamd.c -o $@

# Autoprueba: cifra y descifra un mensaje y comprueba que vuelve igual,
# ademas de fijar la clave derivada de un vector conocido.
nc_selftest: nc_selftest.c $(OBJS)
	$(CC) $(CFLAGS) -o $@ nc_selftest.c $(OBJS)

test: nc_selftest
	./nc_selftest

install: $(LIB)
	install -d $(DESTDIR)$(LIBDIR)
	install -m 0644 $(LIB) $(DESTDIR)$(LIBDIR)/$(LIB)
	-ldconfig
	@echo "Instalado en $(DESTDIR)$(LIBDIR)/$(LIB)"

clean:
	rm -f $(OBJS) $(LIB) nc_selftest
