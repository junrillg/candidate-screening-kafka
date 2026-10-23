import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import org.mindrot.jbcrypt.BCrypt;

public final class HashPassword {
    public static void main(String[] args) {
        byte[] input = null;
        boolean failed = false;
        try {
            if (args.length != 0) {
                throw new IllegalArgumentException();
            }
            input = System.in.readNBytes(75);
            String password = decodePassword(input);
            System.out.println(BCrypt.hashpw(password, BCrypt.gensalt(12)));
            if (System.out.checkError()) {
                throw new IOException();
            }
        } catch (IOException | IllegalArgumentException error) {
            System.err.println("Password hashing rejected.");
            failed = true;
        } finally {
            if (input != null) {
                Arrays.fill(input, (byte) 0);
            }
        }
        if (failed) {
            System.exit(1);
        }
    }

    static String decodePassword(byte[] input) throws CharacterCodingException {
        int length = input.length;
        if (length > 0 && input[length - 1] == '\n') {
            length--;
            if (length > 0 && input[length - 1] == '\r') {
                length--;
            }
        }
        // BCrypt truncates beyond 72 UTF-8 bytes; reject instead of silently changing a password.
        if (length < 1 || length > 72) {
            throw new IllegalArgumentException();
        }
        for (int index = 0; index < length; index++) {
            if (input[index] == 0 || input[index] == '\r' || input[index] == '\n') {
                throw new IllegalArgumentException();
            }
        }
        return StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(input, 0, length)).toString();
    }
}

final class HashPasswordSelfTest {
    public static void main(String[] args) throws CharacterCodingException {
        String password = "synthetic-auth-tool-fixture";
        for (String ending : new String[] {"", "\n", "\r\n"}) {
            require(HashPassword.decodePassword((password + ending).getBytes(StandardCharsets.UTF_8))
                    .equals(password));
        }
        reject(new byte[0]);
        reject("first\nsecond".getBytes(StandardCharsets.UTF_8));
        reject(new byte[] {'a', 0, 'b'});
        reject(new byte[] {(byte) 0xc3, 0x28});
        byte[] tooLong = new byte[73];
        Arrays.fill(tooLong, (byte) 'x');
        reject(tooLong);
        require(HashPassword.decodePassword(Arrays.copyOf(tooLong, 72)).length() == 72);
        require(HashPassword.decodePassword("\u00e9".repeat(36).getBytes(StandardCharsets.UTF_8)).length() == 36);
        reject("\u00e9".repeat(37).getBytes(StandardCharsets.UTF_8));
        String hash = BCrypt.hashpw(password, BCrypt.gensalt(12));
        require(hash.length() == 60 && hash.startsWith("$2a$12$"));
        require(BCrypt.checkpw(password, hash));
        require(!BCrypt.checkpw("synthetic-wrong-fixture", hash));
        try {
            BCrypt.checkpw(password, "$2y$" + hash.substring(4));
            throw new AssertionError("Unsupported BCrypt revision accepted.");
        } catch (IllegalArgumentException expected) {
            // The pinned application library accepts 2a, not htpasswd's 2y revision.
        }
        System.out.println("Auth tool self-check passed.");
    }

    private static void reject(byte[] input) {
        try {
            HashPassword.decodePassword(input);
            throw new AssertionError("Invalid password input accepted.");
        } catch (CharacterCodingException | IllegalArgumentException expected) {
            // Rejected without echoing input or exception details.
        }
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw new AssertionError("Auth tool self-check failed.");
        }
    }
}
