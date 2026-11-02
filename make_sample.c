#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Synthetic fixture only: dark background, one bright object crossing once. */
#define W 80
#define H 80

int main(int argc, char **argv) {
    FILE *f;
    unsigned char y[W * H], uv[W * H / 2];
    int frame, x, row, top;
    if (argc != 2) return 2;
    f = fopen(argv[1], "wb");
    if (!f) return 1;
    memset(uv, 128, sizeof(uv));
    fputs("YUV4MPEG2 W80 H80 F1:1 Ip A1:1 C420jpeg\n", f);
    for (frame = 0; frame <= 61; ++frame) {
        memset(y, 20, sizeof(y));
        if (frame >= 1 && frame <= 12) {
            top = 12 + frame * 4;
            for (row = top; row < top + 8; ++row)
                for (x = 35; x < 45; ++x) y[row * W + x] = 220;
        }
        fputs("FRAME\n", f);
        if (fwrite(y, 1, sizeof(y), f) != sizeof(y) ||
            fwrite(uv, 1, sizeof(uv), f) != sizeof(uv)) {
            fclose(f);
            return 1;
        }
    }
    if (fclose(f) != 0) return 1;
    return 0;
}
