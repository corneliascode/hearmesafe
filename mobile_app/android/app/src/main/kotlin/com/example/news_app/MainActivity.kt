package com.example.hear_me_safe

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import com.google.mediapipe.tasks.audio.audioclassifier.AudioClassifier
import com.google.mediapipe.tasks.audio.audioclassifier.AudioClassifier.AudioClassifierOptions
import com.google.mediapipe.tasks.components.containers.AudioData
import com.google.mediapipe.tasks.components.containers.ClassificationResult
import com.google.mediapipe.tasks.components.containers.AudioData.AudioDataFormat
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.audio.core.RunningMode
import java.io.File
import java.io.FileOutputStream
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class WavAudioData(
    val shortArray: ShortArray,
    val sampleRate: Int,
    val numChannels: Int
)

class MainActivity : FlutterActivity() {
    private val CHANNEL = "audio_recognition"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            if (call.method == "classifyAudio") {
                val filePath = call.argument<String>("filePath")
                if (filePath != null) {
                    val classificationResult = classifyAudio(filePath)
                    result.success(classificationResult)
                } else {
                    result.error("INVALID_ARGUMENT", "File path is null", null)
                }
            } else {
                result.notImplemented()
            }
        }
    }

    private fun loadAudio(filePath: String): WavAudioData {
        val audioFile = File(filePath)
        if (!audioFile.exists()) {
            throw IllegalArgumentException("Audio file not found at $filePath")
        }
    
        FileInputStream(audioFile).use { fis ->
            val header = ByteArray(44) // WAV header is 44 bytes
            fis.read(header)
    
            val sampleRate = ByteBuffer.wrap(header, 24, 4).order(ByteOrder.LITTLE_ENDIAN).int
            val bitsPerSample = ByteBuffer.wrap(header, 34, 2).order(ByteOrder.LITTLE_ENDIAN).short
            val numChannels = ByteBuffer.wrap(header, 22, 2).order(ByteOrder.LITTLE_ENDIAN).short

            // Correct comparison
            if (bitsPerSample.toInt() != 16) {
                throw IllegalArgumentException("Only 16-bit PCM WAV files are supported")
            }
    
            // Read the PCM data from the WAV file
            val pcmData = ByteArray(audioFile.length().toInt() - 44) // Remaining data after header
            fis.read(pcmData)
    
            // Convert the PCM data to short[]
            val shortData = ByteBuffer.wrap(pcmData).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
            val shortArray = ShortArray(shortData.remaining())
            shortData.get(shortArray)
    
            // Now you have short[] which represents your audio samples
            return WavAudioData(shortArray, sampleRate, numChannels.toInt())
        }
    }

    fun findMatchingCategoryNames(
        classificationResults: List<ClassificationResult>, 
        targetStrings: List<String>
    ): String {
        val matchingCategoryNames = mutableListOf<String>()

        // Loop through each ClassificationResult
        for (classificationResult in classificationResults) {
            // Loop through each Classifications object in ClassificationResult
            classificationResult.classifications().forEach { classifications ->
                // Loop through each Category in Classifications
                classifications.categories().forEach { category ->
                    // Check if the categoryName matches any target string
                    matchingCategoryNames.add(category.categoryName() + ':' + category.score().toString())
                }
            }
        }

        // Join the matching category names with a delimiter or return an empty string
        return matchingCategoryNames.joinToString(separator = ", ").ifEmpty { "" }
    }


    private fun classifyAudio(filePath: String): String {
        return try {
            val file = File(filePath)
            if (!file.exists()) {
                return "File not found"
            }

            // Initialize the AudioClassifier
            val assetManager = assets
            val modelInputStream = assetManager.open("yamnet.tflite")
            val modelFile = File(filesDir, "yamnet.tflite")
            
            // Copy the model to internal storage if it doesn't exist
            if (!modelFile.exists()) {
                modelInputStream.use { input ->
                    FileOutputStream(modelFile).use { output ->
                        input.copyTo(output)
                    }
                }
            }

            val baseOptionsBuilder = BaseOptions.builder()

            baseOptionsBuilder.setModelAssetPath(modelFile.absolutePath)

            val baseOptions = baseOptionsBuilder.build()

            val optionsBuilder = AudioClassifier.AudioClassifierOptions.builder().setScoreThreshold(0.01f).setBaseOptions(baseOptions).setRunningMode(RunningMode.AUDIO_CLIPS)

            var options = optionsBuilder.build()

            var audioClassifier = AudioClassifier.createFromOptions(this, options)

            // val audioClassifier = AudioClassifier.createFromFile(this, modelFile.absolutePath)

            val wavAudio = loadAudio(filePath);

            // Create an AudioData object with proper format
            val audioData = AudioData.create(
                AudioData.AudioDataFormat.builder()
                    .setNumOfChannels(wavAudio.numChannels) // Assuming mono audio
                    .setSampleRate(wavAudio.sampleRate.toFloat())
                    .build(),
                wavAudio.sampleRate
            )

            // Load the float array into AudioData
            audioData.load(wavAudio.shortArray)

            var targetStrings = listOf(
                "Speech",
                "Child speech, kid speaking",
                "Whispering",
                "Babbling",
                "Laughter",
                "Baby laughter",
                "Giggle",
                "Snicker",
                "Belly laugh",
                "Chuckle, chortle",
                "Crying, sobbing",
                "Baby cry, infant cry",
                "Whimper",
                "Wail, moan",
                "Sigh",
                "Singing",
                "Choir",
                "Yodeling",
                "Chant",
                "Mantra",
                "Child singing",
                "Synthetic singing",
                "Rapping",
                "Humming",
                "Groan",
                "Grunt",
                "Whistling",
                "Hubbub, speech noise, speech babble",
                "Chatter",
                "Children shouting",
                "Screaming",
                "Beatboxing",
                "Yell",
                "Whoop",
                "Shout",
                "Speech synthesizer",
                "Narration, monologue",
                "Conversation"
              )

            // Classify the audio
            val result = audioClassifier.classify(audioData)
            if(targetStrings.any { target -> result.classificationResults().first().classifications().first().categories().first().categoryName().contains(target, ignoreCase = true) }) {
                result.toString()
            } else {
                ""
            }
        } catch (e: Exception) {
            e.printStackTrace()
            "Error processing audio: ${e.message}"
        }
    }
}